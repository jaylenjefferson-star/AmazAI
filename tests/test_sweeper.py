"""The sweeper must see every owner, not a hardcoded default.

`handlers.sweeper.handler` used to build its one `Store` from
`os.environ.get("OWNER_ID", "owner")`. No deployed Lambda ever set `OWNER_ID`,
and the EventBridge rule that fires the sweeper passes no input either, so in
production every sweep ran scoped to the literal string `"owner"` -- which
matches no real tenant's id (a Google/Auth0 subject like `google-oauth2|...`).
Every read `Store` makes is owner-filtered, so this was not "sweeps less
often" -- it was "has never once resumed or expired anything for a real
account." These tests are the regression guard for that, plus the two jobs
the sweeper is actually supposed to do.
"""

from datetime import datetime, timedelta, timezone

import pytest

from amazai import approvals, keys as K, runs
from amazai.states import RunState

import handlers.sweeper as sweeper

AGENT = {"entity": "Agent", "name": "Engineering", "status": "active",
        "budget": {"maxConcurrentRuns": 3}, "gsi1pk": "AGENTS", "gsi1sk": "Engineering"}


class _FakeLambda:
    def __init__(self):
        self.invocations: list[dict] = []

    def invoke(self, **kwargs):
        self.invocations.append(kwargs)


def _stub_lambda(monkeypatch):
    fake = _FakeLambda()
    monkeypatch.setattr(sweeper.boto3, "client", lambda *_a, **_k: fake)
    monkeypatch.setenv("ORCHESTRATOR_FN_ARN", "arn:aws:lambda:us-west-2:000:function:orch")
    return fake


def _stale_retrying_run(store, *, agent_id: str, thread_id: str) -> dict:
    """A run parked in RETRYING with a heartbeat and gsi2sk days old --
    exactly the shape the five real stuck runs in production had."""
    store.put({"pk": K.agent_pk(agent_id), "sk": "META", **AGENT, "agentId": agent_id})
    run = runs.create(store, agent_id=agent_id, thread_id=thread_id, goal="hey")
    run = runs.advance(store, run, RunState.PLANNING)
    run = runs.advance(store, run, RunState.EXECUTING)
    run = runs.advance(store, run, RunState.RETRYING, attempt=1)
    old = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(
        timespec="seconds").replace("+00:00", "Z")
    return store.update(run["pk"], "META", {"heartbeatAt": old, "gsi2sk": old})


def _stale_approval(store, *, agent_id: str, thread_id: str) -> dict:
    store.put({"pk": K.agent_pk(agent_id), "sk": "META", **AGENT, "agentId": agent_id})
    run = runs.create(store, agent_id=agent_id, thread_id=thread_id, goal="hey")
    approval_id = "apv_test"
    expired = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(
        timespec="seconds").replace("+00:00", "Z")
    store.put({
        "pk": run["pk"], "sk": K.approval_sk(approval_id),
        "entity": "Approval", "approvalId": approval_id,
        "gsi1pk": "APPROVALS", "gsi1sk": f"pending#{expired}",
        "gsi2pk": K.approval_expiry_gsi(), "gsi2sk": expired,
        "runId": run["runId"], "threadId": run["threadId"],
        "action": "email.send", "arguments": {}, "status": approvals.PENDING,
        "expiresAt": expired, "decidedAt": None, "note": None, "consumedAt": None,
    })
    return run, approval_id


class TestEveryOwnerIsSwept:
    def test_no_owner_id_configured_still_sweeps_real_owners(self, two_stores, monkeypatch):
        """The exact production bug: no OWNER_ID env var, no ownerId in the
        event -- what an EventBridge `rate(5 minutes)` invocation actually
        looks like."""
        monkeypatch.delenv("OWNER_ID", raising=False)
        store_a, store_b = two_stores
        fake = _stub_lambda(monkeypatch)
        _stale_retrying_run(store_a, agent_id="eng", thread_id="dm-eng")
        _stale_retrying_run(store_b, agent_id="ops", thread_id="dm-ops")

        result = sweeper.handler({}, None)

        assert result["owners"] == 2
        assert result["runsResumed"] == 2
        assert len(fake.invocations) == 2
        resumed_ids = {i["Payload"] for i in fake.invocations}  # bytes, just uniqueness
        assert len(resumed_ids) == 2

    def test_a_hardcoded_default_owner_would_have_swept_nothing(self, two_stores, monkeypatch):
        """Characterises the bug directly: sweeping only the literal `"owner"`
        default -- the old behaviour -- finds neither real tenant's run."""
        monkeypatch.delenv("OWNER_ID", raising=False)
        store_a, store_b = two_stores
        _stub_lambda(monkeypatch)
        _stale_retrying_run(store_a, agent_id="eng", thread_id="dm-eng")

        result = sweeper.handler({"ownerId": "owner"}, None)

        assert result["owners"] == 1
        assert result["runsResumed"] == 0

    def test_explicit_owner_id_in_event_still_scopes_to_one_owner(self, two_stores, monkeypatch):
        """A manual or test-triggered invocation can still target one tenant
        deliberately -- discovery is the fallback, not the only path."""
        monkeypatch.delenv("OWNER_ID", raising=False)
        store_a, store_b = two_stores
        fake = _stub_lambda(monkeypatch)
        _stale_retrying_run(store_a, agent_id="eng", thread_id="dm-eng")
        _stale_retrying_run(store_b, agent_id="ops", thread_id="dm-ops")

        result = sweeper.handler({"ownerId": store_a.owner_id}, None)

        assert result["owners"] == 1
        assert result["runsResumed"] == 1
        assert len(fake.invocations) == 1

    def test_an_owner_with_no_bots_is_not_a_tenant_yet(self, table, monkeypatch):
        """`discover_owner_ids` scans the AGENTS index; an owner who has
        created nothing is not yet a tenant a sweep needs to protect."""
        monkeypatch.delenv("OWNER_ID", raising=False)
        _stub_lambda(monkeypatch)
        result = sweeper.handler({}, None)
        assert result["owners"] == 0
        assert result["runsResumed"] == 0


class TestStaleRunRecovery:
    def test_a_fresh_run_is_left_alone(self, store, monkeypatch):
        monkeypatch.delenv("OWNER_ID", raising=False)
        fake = _stub_lambda(monkeypatch)
        store.put({"pk": K.agent_pk("eng"), "sk": "META", **AGENT, "agentId": "eng"})
        run = runs.create(store, agent_id="eng", thread_id="dm-eng", goal="hey")
        runs.advance(store, run, RunState.PLANNING)

        result = sweeper.handler({}, None)

        assert result["owners"] == 1, "the owner must be discovered even though nothing was stale"
        assert result["runsResumed"] == 0
        assert fake.invocations == []

    def test_a_run_with_a_tool_call_in_flight_is_failed_not_retried(self, store, monkeypatch):
        """A worker that died mid tool-call may or may not have taken effect;
        the sweeper must surface that, never guess by retrying."""
        monkeypatch.delenv("OWNER_ID", raising=False)
        fake = _stub_lambda(monkeypatch)
        store.put({"pk": K.agent_pk("eng"), "sk": "META", **AGENT, "agentId": "eng"})
        run = runs.create(store, agent_id="eng", thread_id="dm-eng", goal="hey")
        run = runs.advance(store, run, RunState.PLANNING)
        run = runs.advance(store, run, RunState.EXECUTING,
                           pending={"kind": "tool", "toolUseId": "t1"})
        old = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(
            timespec="seconds").replace("+00:00", "Z")
        store.update(run["pk"], "META", {"heartbeatAt": old, "gsi2sk": old})

        result = sweeper.handler({}, None)

        assert result["runsFailed"] == 1
        assert result["runsResumed"] == 0
        assert fake.invocations == []
        fresh = store.get(run["pk"], "META")
        assert fresh["state"] == RunState.FAILED.value


class TestApprovalExpiry:
    def test_a_pending_approval_past_its_deadline_expires_for_the_real_owner(
            self, store, monkeypatch):
        monkeypatch.delenv("OWNER_ID", raising=False)
        _stub_lambda(monkeypatch)
        run, approval_id = _stale_approval(store, agent_id="eng", thread_id="dm-eng")

        result = sweeper.handler({}, None)

        assert result["approvalsExpired"] == 1
        decided = store.get(run["pk"], K.approval_sk(approval_id))
        assert decided["status"] == approvals.EXPIRED
