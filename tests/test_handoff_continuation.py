"""Handoff acceptance and coordinator continuation.

Covers the two things `_record_handoff` never used to do: accept a proposed
handoff (automatically, when the receiver already clears grant/budget/policy,
or by a human through the new API route) and wake whoever proposed it once
the work it started actually finishes. See `amazai/handoffs.py`.
"""

from __future__ import annotations

import types

import pytest

from amazai import collab, handoffs, keys as K, runs
from amazai.cost import RunCost
from amazai.evidence import EvidenceWriter
from amazai.push import Push
from amazai.states import RunState
from amazai.store import Conflict

import handlers.orchestrator as orch
from tests.test_agents_api import api_table, call  # noqa: F401

COORDINATOR = {"agentId": "eng", "name": "Engineering", "status": "active",
              "budget": {"maxConcurrentRuns": 3}}
RECEIVER = {"agentId": "ops", "name": "Cloud Operations", "status": "active",
           "budget": {"maxConcurrentRuns": 3}}


class RecPush(Push):
    def __init__(self):
        self.sent = []

    def send(self, payload):
        self.sent.append(payload)
        return 0


def _tool_call(name, args, tool_use_id="tu-1"):
    return types.SimpleNamespace(tool_name=name, tool_input=args, tool_use_id=tool_use_id)


@pytest.fixture
def agents(store):
    store.put({"pk": K.agent_pk("eng"), "sk": "META", "entity": "Agent", **COORDINATOR})
    store.put({"pk": K.agent_pk("ops"), "sk": "META", "entity": "Agent", **RECEIVER})
    return store


@pytest.fixture
def coordinator_run(agents):
    return runs.create(agents, agent_id="eng", thread_id="room-1", goal="ship the migration")


@pytest.fixture
def proposed_handoff(agents, coordinator_run):
    return orch._record_handoff(agents, coordinator_run, {
        "to": "ops", "goal": "run the migration script", "requestedAction": "",
    })


class TestCanAutoAccept:
    def test_ordinary_work_with_no_named_action_clears(self, agents, proposed_handoff):
        receiver = agents.get(K.agent_pk("ops"), "META")
        ok, reason = handoffs.can_auto_accept(agents, proposed_handoff, receiver)
        assert ok, reason

    def test_an_action_on_the_always_approve_floor_never_auto_accepts(self, agents, coordinator_run):
        handoff = orch._record_handoff(agents, coordinator_run, {
            "to": "ops", "goal": "send the announcement", "requestedAction": "email.send"})
        receiver = agents.get(K.agent_pk("ops"), "META")
        ok, reason = handoffs.can_auto_accept(agents, handoff, receiver)
        assert not ok
        assert "always-approve floor" in reason

    def test_never_approvable_never_auto_accepts(self, agents, coordinator_run):
        handoff = orch._record_handoff(agents, coordinator_run, {
            "to": "ops", "goal": "grant org admin", "requestedAction": "org.admin.grant"})
        receiver = agents.get(K.agent_pk("ops"), "META")
        ok, reason = handoffs.can_auto_accept(agents, handoff, receiver)
        assert not ok

    def test_a_dotted_action_needs_the_receiver_to_hold_that_apps_grant(self, agents, coordinator_run):
        handoff = orch._record_handoff(agents, coordinator_run, {
            "to": "ops", "goal": "post the status", "requestedAction": "slack.post_message"})
        receiver = agents.get(K.agent_pk("ops"), "META")
        ok, reason = handoffs.can_auto_accept(agents, handoff, receiver)
        assert not ok
        assert "slack" in reason

    def test_a_receiver_at_its_concurrency_ceiling_does_not_auto_accept(self, agents, proposed_handoff):
        receiver = agents.update(K.agent_pk("ops"), "META", {"budget": {"maxConcurrentRuns": 1}})
        runs.create(agents, agent_id="ops", thread_id="dm-ops", goal="already busy")
        ok, reason = handoffs.can_auto_accept(agents, proposed_handoff, receiver)
        assert not ok
        assert "concurrency" in reason


class TestAccept:
    def test_accepting_spawns_a_child_run_and_binds_it_to_a_task(
            self, agents, coordinator_run, proposed_handoff):
        result = handoffs.accept(agents, coordinator_run, proposed_handoff,
                                 decided_by="system:auto-accept")

        assert result["handoff"]["status"] == "accepted"
        child = result["child"]
        assert child["agentId"] == "ops"
        assert child["trigger"]["coordinatorRunId"] == coordinator_run["runId"]
        assert child["trigger"]["taskId"] == coordinator_run["runId"]

        task = agents.get(K.task_pk(coordinator_run["runId"]), "META")
        assert task["coordinatorAgentId"] == "eng"
        child_row = agents.get(K.task_pk(coordinator_run["runId"]),
                               K.task_child_sk(child["runId"]))
        assert child_row["status"] == "active"

    def test_accepting_writes_a_visible_message_in_the_task(
            self, agents, coordinator_run, proposed_handoff):
        handoffs.accept(agents, coordinator_run, proposed_handoff, decided_by="system:auto-accept")
        messages = [r for r in agents.query(K.thread_pk(coordinator_run["threadId"]), sk_prefix="MSG#")
                   if r.get("entity") == "AgentMessage"]
        assert len(messages) == 1
        assert messages[0]["senderAgentId"] == "eng"
        assert messages[0]["recipientAgentId"] == "ops"

    def test_accepting_twice_loses_the_race_on_the_second_call(
            self, agents, coordinator_run, proposed_handoff):
        handoffs.accept(agents, coordinator_run, proposed_handoff, decided_by="system:auto-accept")
        with pytest.raises(Conflict):
            handoffs.accept(agents, coordinator_run, proposed_handoff, decided_by="user:someone")

    def test_hop_depth_refuses_the_accept_rather_than_silently_waking_no_one(
            self, agents, coordinator_run, proposed_handoff):
        """`accept()` reuses `collab.send`'s own hop/volume ceiling. A handoff
        that cannot clear it must not spawn a child -- and must not leave the
        handoff silently `proposed` forever either; the caller decides that."""
        agents.put({"pk": K.user_pk(agents.owner_id), "sk": "META", "entity": "User",
                   "policy": {"messaging": {"maxHopDepth": 0}}})
        with pytest.raises(collab.MessagingError):
            handoffs.accept(agents, coordinator_run, proposed_handoff, decided_by="system:auto-accept")


class TestNotifyCoordinatorIfChild:
    def test_a_run_with_no_coordinator_is_not_a_child(self, agents):
        run = runs.create(agents, agent_id="eng", thread_id="dm-eng", goal="hi")
        result = handoffs.notify_coordinator_if_child(agents, run, RunState.COMPLETED.value, "done")
        assert result is None

    def test_a_childs_completion_wakes_the_coordinator_exactly_once(
            self, agents, coordinator_run, proposed_handoff):
        accepted = handoffs.accept(agents, coordinator_run, proposed_handoff,
                                   decided_by="system:auto-accept")
        child = accepted["child"]

        first = handoffs.notify_coordinator_if_child(
            agents, child, RunState.COMPLETED.value, "migration script ran clean")
        second = handoffs.notify_coordinator_if_child(
            agents, child, RunState.COMPLETED.value, "migration script ran clean")

        assert first is not None
        assert second is None, "a duplicate settle of the same run must not wake it twice"
        assert first["agentId"] == "eng"
        assert first["trigger"]["type"] == "child_completion"
        assert first["trigger"]["childRunId"] == child["runId"]
        assert "migration script ran clean" in first["goal"]

        child_row = agents.get(K.task_pk(coordinator_run["runId"]), K.task_child_sk(child["runId"]))
        assert child_row["status"] == "done"

    def test_a_failed_child_is_reported_as_failed_not_silently_dropped(
            self, agents, coordinator_run, proposed_handoff):
        accepted = handoffs.accept(agents, coordinator_run, proposed_handoff,
                                   decided_by="system:auto-accept")
        continuation = handoffs.notify_coordinator_if_child(
            agents, accepted["child"], RunState.FAILED.value, "the script threw on step 3")
        assert continuation is not None
        assert continuation["trigger"]["outcome"] == "failed"
        assert "hit a problem" in continuation["goal"]


class TestHandoffToolAutoAccepts:
    """Through the real dispatch a model's `handoff` tool call goes through --
    `orchestrator._handle_tool` -- not a reimplementation of it."""

    def _handle(self, store, run, agent, args):
        push, ev, cost, turn = RecPush(), EvidenceWriter(run["runId"]), RunCost(), orch.Turn()
        return orch._handle_tool(store, run, agent, ev, push, resolution=None,
                                 parsed=_tool_call("handoff", args), seq=1,
                                 cost=cost, turn=turn), push

    def test_ordinary_handoff_is_accepted_and_the_receiver_is_woken(
            self, agents, coordinator_run, monkeypatch):
        woken = []
        monkeypatch.setattr(orch, "_invoke_orchestrator_async",
                            lambda run_id, owner: woken.append(run_id))
        agent = agents.get(K.agent_pk("eng"), "META")

        result, push = self._handle(agents, coordinator_run, agent,
                                    {"to": "ops", "goal": "run the migration script"})

        assert result["toolResult"]["status"] == "accepted"
        assert len(woken) == 1
        handoff_events = [e for e in push.sent if e["type"] == "handoff"]
        assert handoff_events[0]["handoff"]["status"] == "accepted"

    def test_a_floor_action_stays_proposed_for_a_human(self, agents, coordinator_run, monkeypatch):
        woken = []
        monkeypatch.setattr(orch, "_invoke_orchestrator_async",
                            lambda run_id, owner: woken.append(run_id))
        agent = agents.get(K.agent_pk("eng"), "META")

        result, _push = self._handle(agents, coordinator_run, agent,
                                     {"to": "ops", "goal": "send the announcement",
                                      "requestedAction": "email.send"})

        assert result["toolResult"]["status"] == "proposed"
        assert woken == []


class TestManualHandoffRoute:
    def _propose(self, table):
        from amazai.store import Store
        store = Store("owner-a", table=table)
        store.put({"pk": K.agent_pk("eng"), "sk": "META", "entity": "Agent", **COORDINATOR})
        store.put({"pk": K.agent_pk("ops"), "sk": "META", "entity": "Agent", **RECEIVER})
        run = runs.create(store, agent_id="eng", thread_id="room-1", goal="ship it")
        handoff = orch._record_handoff(store, run, {
            "to": "ops", "goal": "send the announcement", "requestedAction": "email.send"})
        return store, run, handoff

    def test_accepting_through_the_api_spawns_the_child(self, api_table, monkeypatch):
        monkeypatch.setattr(orch, "_invoke_orchestrator_async", lambda *a, **k: None)
        import handlers.api as api
        monkeypatch.setattr(api, "_invoke_orchestrator", lambda *a, **k: None)
        store, run, handoff = self._propose(api_table)

        status, body = call("POST", f"/handoffs/{run['runId']}/{handoff['handoffId']}",
                            {"approve": True})

        assert status == 200, body
        assert body["handoff"]["status"] == "accepted"
        assert body["childRunId"]

    def test_rejecting_through_the_api_leaves_no_child(self, api_table, monkeypatch):
        import handlers.api as api
        monkeypatch.setattr(api, "_invoke_orchestrator", lambda *a, **k: None)
        store, run, handoff = self._propose(api_table)

        status, body = call("POST", f"/handoffs/{run['runId']}/{handoff['handoffId']}",
                            {"approve": False, "note": "not now"})

        assert status == 200, body
        assert body["handoff"]["status"] == "rejected"

    def test_deciding_twice_is_a_conflict_not_a_second_child(self, api_table, monkeypatch):
        import handlers.api as api
        monkeypatch.setattr(api, "_invoke_orchestrator", lambda *a, **k: None)
        store, run, handoff = self._propose(api_table)
        status, _ = call("POST", f"/handoffs/{run['runId']}/{handoff['handoffId']}", {"approve": True})
        assert status == 200

        status, body = call("POST", f"/handoffs/{run['runId']}/{handoff['handoffId']}", {"approve": True})
        assert status == 409, body
