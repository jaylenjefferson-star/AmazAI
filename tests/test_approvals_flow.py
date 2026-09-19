"""End-to-end approval and run lifecycle against a mocked DynamoDB."""
from datetime import datetime, timedelta, timezone

import pytest

from amazai import approvals, keys as K, runs
from amazai.policy import Capability
from amazai.states import RunState

# `store` comes from tests/conftest.py, which owns the one table definition.


@pytest.fixture
def run(store):
    return runs.create(store, agent_id="eng", thread_id="th-1",
                       goal="Fix the flaky test and open a PR")


class TestRunCreation:
    def test_run_starts_queued(self, run):
        assert run["state"] == RunState.QUEUED.value

    def test_session_id_meets_the_minimum(self, run):
        assert len(run["sessionId"]) >= K.MIN_SESSION_ID_LEN

    def test_sweeper_index_is_populated(self, run):
        assert run["gsi2pk"] == "RUNSTATE#QUEUED"

    def test_deadline_is_set(self, run):
        assert run["deadlineAt"]


class TestRunAdvance:
    def test_legal_progression(self, store, run):
        r = runs.advance(store, run, RunState.PLANNING)
        r = runs.advance(store, r, RunState.EXECUTING)
        assert r["state"] == RunState.EXECUTING.value
        assert r["gsi2pk"] == "RUNSTATE#EXECUTING"

    def test_illegal_transition_raises_before_writing(self, store, run):
        from amazai.states import InvalidTransition
        with pytest.raises(InvalidTransition):
            runs.advance(store, run, RunState.COMPLETED if False else RunState.EXECUTING)
        # Unchanged on disk.
        assert store.get(run["pk"], "META")["state"] == RunState.QUEUED.value

    def test_terminal_sets_ended_at(self, store, run):
        r = runs.advance(store, run, RunState.FAILED)
        assert r["endedAt"]


class TestApprovalRoundTrip:
    def _pause(self, store, run):
        r = runs.advance(store, run, RunState.PLANNING)
        r = runs.advance(store, r, RunState.EXECUTING)
        apv = approvals.request(
            store, r, action="pr.create",
            arguments={"repo": "amazai", "head": "fix/flaky"},
            why="Open the PR for the test fix",
            capability=Capability.WRITE, tool_use_id="tu-1",
        )
        return runs.pause_for_approval(store, r, apv), apv

    def test_run_pauses_with_pending_recorded(self, store, run):
        r, apv = self._pause(store, run)
        assert r["state"] == RunState.AWAITING_APPROVAL.value
        assert r["pending"]["approvalId"] == apv["approvalId"]
        assert r["pending"]["toolUseId"] == "tu-1"

    def test_approve_then_resume(self, store, run):
        r, apv = self._pause(store, run)
        decided = approvals.decide(store, r["pk"], apv["approvalId"], approve=True)
        assert decided["status"] == approvals.APPROVED
        resumed = runs.advance(store, store.get(r["pk"], "META"),
                               RunState.EXECUTING, pending=None)
        assert resumed["state"] == RunState.EXECUTING.value

    def test_deny_is_recorded_with_the_note(self, store, run):
        r, apv = self._pause(store, run)
        decided = approvals.decide(store, r["pk"], apv["approvalId"],
                                   approve=False, note="wrong branch")
        assert decided["status"] == approvals.DENIED
        assert decided["note"] == "wrong branch"

    def test_a_decision_cannot_be_made_twice(self, store, run):
        from amazai.store import Conflict
        r, apv = self._pause(store, run)
        approvals.decide(store, r["pk"], apv["approvalId"], approve=True)
        with pytest.raises(Conflict):
            approvals.decide(store, r["pk"], apv["approvalId"], approve=False)

    def test_changed_arguments_invalidate_the_approval(self, store, run):
        r, apv = self._pause(store, run)
        # The substitution gap: approval was for head=fix/flaky.
        with pytest.raises(PermissionError):
            approvals.decide(store, r["pk"], apv["approvalId"], approve=True,
                             arguments={"repo": "amazai", "head": "main"})

    def test_matching_arguments_are_accepted(self, store, run):
        r, apv = self._pause(store, run)
        decided = approvals.decide(
            store, r["pk"], apv["approvalId"], approve=True,
            arguments={"repo": "amazai", "head": "fix/flaky"})
        assert decided["status"] == approvals.APPROVED


class TestExpiry:
    def test_expiry_denies_rather_than_granting(self, store, run):
        apv = approvals.request(store, run, action="pr.merge", arguments={},
                                why="merge", capability=Capability.WRITE)
        expired = approvals.expire(store, run["pk"], apv["approvalId"])
        assert expired["status"] == approvals.EXPIRED
        assert "denied" in expired["note"]

    def test_deciding_an_already_expired_approval_is_refused(self, store, run):
        past = (datetime.now(timezone.utc) - timedelta(minutes=1))
        apv = approvals.request(store, run, action="pr.merge", arguments={},
                                why="merge", capability=Capability.WRITE)
        store.update(run["pk"], K.approval_sk(apv["approvalId"]),
                     {"expiresAt": past.isoformat(timespec="seconds").replace("+00:00", "Z")})
        with pytest.raises(PermissionError):
            approvals.decide(store, run["pk"], apv["approvalId"], approve=True)

    def test_risky_capability_gets_the_short_window(self, store, run):
        apv = approvals.request(store, run, action="aws.change.ecs", arguments={},
                                why="scale", capability=Capability.DESTRUCTIVE)
        delta = (datetime.fromisoformat(apv["expiresAt"].replace("Z", "+00:00"))
                 - datetime.now(timezone.utc))
        assert delta <= timedelta(minutes=15)

    def test_due_for_expiry_finds_overdue_pending_approvals(self, store, run):
        apv = approvals.request(store, run, action="pr.merge", arguments={},
                                why="merge", capability=Capability.DESTRUCTIVE)
        future = datetime.now(timezone.utc) + timedelta(days=1)
        due = approvals.due_for_expiry(store, now=future)
        assert apv["approvalId"] in [a["approvalId"] for a in due]

    def test_decided_approvals_leave_the_expiry_index(self, store, run):
        apv = approvals.request(store, run, action="pr.create", arguments={},
                                why="x", capability=Capability.WRITE)
        approvals.decide(store, run["pk"], apv["approvalId"], approve=True)
        future = datetime.now(timezone.utc) + timedelta(days=2)
        due = approvals.due_for_expiry(store, now=future)
        assert apv["approvalId"] not in [a["approvalId"] for a in due]


class TestApprovalCard:
    def test_card_is_built_from_the_tool_call(self, store, run):
        # Rendered from arguments, never from model prose: an injected model
        # must not be able to write its own approval card.
        apv = approvals.request(
            store, run, action="aws.change.ecs",
            arguments={"service": "api", "desiredCount": 4},
            why="CPU sustained above 90%", capability=Capability.COST,
            target={"account": "123456789012", "env": "production"},
            reversible=True)
        card = approvals.to_card(apv)
        assert card["action"] == "aws.change.ecs"
        assert card["arguments"]["desiredCount"] == 4
        assert card["target"]["env"] == "production"
        assert card["risk"] == "high"
        assert card["reversible"] is True
        assert "binding" not in card


class TestCancellation:
    def test_cancel_flag_is_visible_to_the_orchestrator(self, store, run):
        r = runs.advance(store, run, RunState.PLANNING)
        r = runs.advance(store, r, RunState.EXECUTING)
        runs.advance(store, r, RunState.CANCELLING)
        assert runs.is_cancelled(store, r)

    def test_running_run_is_not_cancelled(self, store, run):
        assert not runs.is_cancelled(store, run)


class TestStaleness:
    def test_fresh_heartbeat_is_not_stale(self, run):
        assert not runs.heartbeat_stale(run, minutes=10)

    def test_old_heartbeat_is_stale(self, run):
        future = datetime.now(timezone.utc) + timedelta(minutes=30)
        assert runs.heartbeat_stale(run, minutes=10, now=future)

    def test_deadline_detection(self, run):
        future = datetime.now(timezone.utc) + timedelta(hours=2)
        assert runs.deadline_passed(run, now=future)
        assert not runs.deadline_passed(run)
