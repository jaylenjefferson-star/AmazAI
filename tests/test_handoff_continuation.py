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
from amazai.store import Conflict, Store

import handlers.orchestrator as orch
from tests.test_agents_api import api_table, call  # noqa: F401

COORDINATOR = {"agentId": "eng", "name": "Engineering", "status": "active",
              "budget": {"maxConcurrentRuns": 3}}
RECEIVER = {"agentId": "ops", "name": "Cloud Operations", "status": "active",
           "budget": {"maxConcurrentRuns": 3}}
THIRD = {"agentId": "clo", "name": "Counsel", "status": "active",
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
    store.put({"pk": K.agent_pk(store.owner_id, "eng"), "sk": "META", "entity": "Agent", **COORDINATOR})
    store.put({"pk": K.agent_pk(store.owner_id, "ops"), "sk": "META", "entity": "Agent", **RECEIVER})
    return store


@pytest.fixture
def coordinator_run(agents):
    return runs.create(agents, agent_id="eng", thread_id="room-1", goal="ship the migration")


@pytest.fixture
def proposed_handoff(agents, coordinator_run):
    return orch._record_handoff(agents, coordinator_run, {
        "to": "ops", "goal": "run the migration script", "requestedAction": "",
    })


class TestMultiChildFanIn:
    """Two independent handoffs from the same task -- the exact shape a
    'split across two bots' request produces. The coordinator must wake
    exactly once, after *both* finish, never after just one, and never
    twice."""

    @pytest.fixture
    def agents3(self, agents):
        agents.put({"pk": K.agent_pk(agents.owner_id, "clo"), "sk": "META", "entity": "Agent", **THIRD})
        return agents

    def test_the_coordinator_does_not_wake_until_every_child_reports(
            self, agents3, coordinator_run):
        h1 = orch._record_handoff(agents3, coordinator_run, {"to": "ops", "goal": "task one"})
        h2 = orch._record_handoff(agents3, coordinator_run, {"to": "clo", "goal": "task two"})
        a1 = handoffs.accept(agents3, coordinator_run, h1, decided_by="system:auto-accept")
        a2 = handoffs.accept(agents3, coordinator_run, h2, decided_by="system:auto-accept")

        task = agents3.get(K.task_pk(coordinator_run["runId"]), "META")
        assert task["pendingChildren"] == 2

        woken_after_first = handoffs.notify_coordinator_if_child(
            agents3, a1["child"], RunState.COMPLETED.value, "task one done")
        assert woken_after_first is None, "one sibling still outstanding -- must not wake yet"

        woken_after_second = handoffs.notify_coordinator_if_child(
            agents3, a2["child"], RunState.COMPLETED.value, "task two done")
        assert woken_after_second is not None
        assert woken_after_second["trigger"]["childCount"] == 2
        assert woken_after_second["trigger"]["doneCount"] == 2
        assert "task one done" in woken_after_second["goal"]
        assert "task two done" in woken_after_second["goal"]

    def test_order_of_completion_does_not_matter(self, agents3, coordinator_run):
        """The *second* child to be accepted can be the *first* to finish --
        still exactly one wake, on whichever settle is actually last."""
        h1 = orch._record_handoff(agents3, coordinator_run, {"to": "ops", "goal": "task one"})
        h2 = orch._record_handoff(agents3, coordinator_run, {"to": "clo", "goal": "task two"})
        a1 = handoffs.accept(agents3, coordinator_run, h1, decided_by="system:auto-accept")
        a2 = handoffs.accept(agents3, coordinator_run, h2, decided_by="system:auto-accept")

        first = handoffs.notify_coordinator_if_child(
            agents3, a2["child"], RunState.COMPLETED.value, "task two done")
        second = handoffs.notify_coordinator_if_child(
            agents3, a1["child"], RunState.COMPLETED.value, "task one done")

        assert first is None
        assert second is not None
        assert second["trigger"]["childCount"] == 2

    def test_a_partial_batch_is_reported_as_partial_not_hidden(self, agents3, coordinator_run):
        h1 = orch._record_handoff(agents3, coordinator_run, {"to": "ops", "goal": "task one"})
        h2 = orch._record_handoff(agents3, coordinator_run, {"to": "clo", "goal": "task two"})
        a1 = handoffs.accept(agents3, coordinator_run, h1, decided_by="system:auto-accept")
        a2 = handoffs.accept(agents3, coordinator_run, h2, decided_by="system:auto-accept")

        handoffs.notify_coordinator_if_child(
            agents3, a1["child"], RunState.FAILED.value, "ran out of disk")
        woken = handoffs.notify_coordinator_if_child(
            agents3, a2["child"], RunState.COMPLETED.value, "task two done")

        assert woken is not None
        assert woken["trigger"]["doneCount"] == 1
        assert woken["trigger"]["childCount"] == 2
        assert "1 succeeded" in woken["goal"]
        assert "ran out of disk" in woken["goal"]

    def test_a_third_wave_of_handoffs_after_a_wake_still_counts_correctly(
            self, agents3, coordinator_run):
        """`pendingChildren` is a live counter, not a one-shot flag: the
        coordinator can fan out again after waking, and that second wave
        gets its own correct fan-in."""
        h1 = orch._record_handoff(agents3, coordinator_run, {"to": "ops", "goal": "task one"})
        a1 = handoffs.accept(agents3, coordinator_run, h1, decided_by="system:auto-accept")
        first_wake = handoffs.notify_coordinator_if_child(
            agents3, a1["child"], RunState.COMPLETED.value, "task one done")
        assert first_wake is not None

        h2 = orch._record_handoff(agents3, coordinator_run, {"to": "clo", "goal": "task three"})
        a2 = handoffs.accept(agents3, coordinator_run, h2, decided_by="system:auto-accept")
        task = agents3.get(K.task_pk(coordinator_run["runId"]), "META")
        assert task["pendingChildren"] == 1

        second_wake = handoffs.notify_coordinator_if_child(
            agents3, a2["child"], RunState.COMPLETED.value, "task three done")
        assert second_wake is not None
        assert second_wake["trigger"]["childCount"] == 2  # cumulative CHILD# rows on the task


class TestCanAutoAccept:
    def test_ordinary_work_with_no_named_action_clears(self, agents, coordinator_run, proposed_handoff):
        receiver = agents.get(K.agent_pk(agents.owner_id, "ops"), "META")
        ok, reason = handoffs.can_auto_accept(agents, proposed_handoff, receiver, coordinator_run)
        assert ok, reason

    def test_an_action_on_the_always_approve_floor_never_auto_accepts(self, agents, coordinator_run):
        handoff = orch._record_handoff(agents, coordinator_run, {
            "to": "ops", "goal": "send the announcement", "requestedAction": "email.send"})
        receiver = agents.get(K.agent_pk(agents.owner_id, "ops"), "META")
        ok, reason = handoffs.can_auto_accept(agents, handoff, receiver, coordinator_run)
        assert not ok
        assert "always-approve floor" in reason

    def test_never_approvable_never_auto_accepts(self, agents, coordinator_run):
        handoff = orch._record_handoff(agents, coordinator_run, {
            "to": "ops", "goal": "grant org admin", "requestedAction": "org.admin.grant"})
        receiver = agents.get(K.agent_pk(agents.owner_id, "ops"), "META")
        ok, reason = handoffs.can_auto_accept(agents, handoff, receiver, coordinator_run)
        assert not ok

    def test_a_dotted_action_needs_the_receiver_to_hold_that_apps_grant(self, agents, coordinator_run):
        handoff = orch._record_handoff(agents, coordinator_run, {
            "to": "ops", "goal": "post the status", "requestedAction": "slack.post_message"})
        receiver = agents.get(K.agent_pk(agents.owner_id, "ops"), "META")
        ok, reason = handoffs.can_auto_accept(agents, handoff, receiver, coordinator_run)
        assert not ok
        assert "slack" in reason

    def test_a_receiver_at_its_concurrency_ceiling_does_not_auto_accept(
            self, agents, coordinator_run, proposed_handoff):
        receiver = agents.update(K.agent_pk(agents.owner_id, "ops"), "META", {"budget": {"maxConcurrentRuns": 1}})
        runs.create(agents, agent_id="ops", thread_id="dm-ops", goal="already busy")
        ok, reason = handoffs.can_auto_accept(agents, proposed_handoff, receiver, coordinator_run)
        assert not ok
        assert "concurrency" in reason

    def test_a_task_at_its_fan_out_ceiling_does_not_auto_accept(
            self, agents, coordinator_run, proposed_handoff):
        handoffs.ensure_task(agents, coordinator_run["runId"], coordinator_run)
        agents.update(K.task_pk(coordinator_run["runId"]), "META",
                      {"pendingChildren": handoffs.MAX_ACTIVE_CHILDREN_PER_TASK})
        receiver = agents.get(K.agent_pk(agents.owner_id, "ops"), "META")
        ok, reason = handoffs.can_auto_accept(agents, proposed_handoff, receiver, coordinator_run)
        assert not ok
        assert "outstanding" in reason

    def test_a_room_at_its_run_ceiling_does_not_auto_accept(
            self, agents, coordinator_run, proposed_handoff):
        """Wider than the per-task ceiling on purpose: a coordinator cannot
        route around gate 3 by starting a fresh task in the same room each
        time the last one settles -- this counts every run on the thread,
        not just this task's own children."""
        for _ in range(handoffs.MAX_ACTIVE_RUNS_PER_ROOM):
            runs.create(agents, agent_id="ops", thread_id=coordinator_run["threadId"], goal="busy")
        receiver = agents.get(K.agent_pk(agents.owner_id, "ops"), "META")

        ok, reason = handoffs.can_auto_accept(agents, proposed_handoff, receiver, coordinator_run)

        assert not ok
        assert "in flight" in reason

    def test_a_room_under_its_run_ceiling_still_auto_accepts(
            self, agents, coordinator_run, proposed_handoff):
        # Filler runs are `eng`'s, not the receiver's -- this must exercise
        # only the room-wide ceiling, not the receiver's own concurrency one.
        # coordinator_run itself already counts as one active run on the
        # thread, so this leaves the room one run short of the ceiling.
        for _ in range(handoffs.MAX_ACTIVE_RUNS_PER_ROOM - 2):
            runs.create(agents, agent_id="eng", thread_id=coordinator_run["threadId"], goal="busy")
        receiver = agents.get(K.agent_pk(agents.owner_id, "ops"), "META")

        ok, reason = handoffs.can_auto_accept(agents, proposed_handoff, receiver, coordinator_run)

        assert ok, reason


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
        messages = [r for r in agents.query(K.thread_pk(agents.owner_id, coordinator_run["threadId"]), sk_prefix="MSG#")
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
        assert "the script threw on step 3" in continuation["goal"]
        assert "0 succeeded" in continuation["goal"]


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
        agent = agents.get(K.agent_pk(agents.owner_id, "eng"), "META")

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
        agent = agents.get(K.agent_pk(agents.owner_id, "eng"), "META")

        result, _push = self._handle(agents, coordinator_run, agent,
                                     {"to": "ops", "goal": "send the announcement",
                                      "requestedAction": "email.send"})

        assert result["toolResult"]["status"] == "proposed"
        assert woken == []


class TestManualHandoffRoute:
    def _propose(self, table):
        from amazai.store import Store
        store = Store("owner-a", table=table)
        store.put({"pk": K.agent_pk(store.owner_id, "eng"), "sk": "META", "entity": "Agent", **COORDINATOR})
        store.put({"pk": K.agent_pk(store.owner_id, "ops"), "sk": "META", "entity": "Agent", **RECEIVER})
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


class TestTaskReadRoute:
    """The operator's window onto a task that's still fanned out and
    waiting -- `pendingChildren` is the same counter the fan-in logic itself
    relies on, just read back rather than acted on."""

    def test_a_task_with_one_child_still_outstanding(self, api_table, monkeypatch):
        monkeypatch.setattr(orch, "_invoke_orchestrator_async", lambda *a, **k: None)
        import handlers.api as api
        monkeypatch.setattr(api, "_invoke_orchestrator", lambda *a, **k: None)
        store = Store("owner-a", table=api_table)
        store.put({"pk": K.agent_pk(store.owner_id, "eng"), "sk": "META", "entity": "Agent", **COORDINATOR})
        store.put({"pk": K.agent_pk(store.owner_id, "ops"), "sk": "META", "entity": "Agent", **RECEIVER})
        run = runs.create(store, agent_id="eng", thread_id="room-1", goal="ship it")
        handoff = orch._record_handoff(store, run, {"to": "ops", "goal": "run the script"})
        handoffs.accept(store, run, handoff, decided_by="system:auto-accept")

        status, body = call("GET", f"/tasks/{run['runId']}")

        assert status == 200, body
        assert body["pendingChildren"] == 1
        assert body["counts"]["active"] == 1
        assert len(body["children"]) == 1


class TestOrderedHandoffs:
    """Dependent work needs no separate scheduling concept: the second
    handoff cannot exist before the first's completion creates the
    continuation run that proposes it -- ordering is a property of *when* a
    tool call can happen, not a field on the handoff."""

    def test_a_second_handoff_can_only_be_proposed_after_the_first_completes(
            self, agents, coordinator_run):
        h1 = orch._record_handoff(agents, coordinator_run,
                                  {"to": "ops", "goal": "provision the environment"})
        a1 = handoffs.accept(agents, coordinator_run, h1, decided_by="system:auto-accept")

        # Nothing about task two exists yet -- the coordinator has not been
        # woken, so it has had no opportunity to ask for it.
        assert agents.query(K.task_pk(coordinator_run["runId"]), sk_prefix="CHILD#") == \
            [agents.get(K.task_pk(coordinator_run["runId"]), K.task_child_sk(a1["child"]["runId"]))]

        continuation = handoffs.notify_coordinator_if_child(
            agents, a1["child"], RunState.COMPLETED.value, "environment ready")
        assert continuation is not None
        assert continuation["trigger"]["type"] == "child_completion"

        # Only now -- in the run the first completion itself created -- can
        # the dependent second step be proposed. It is bound to the *same*
        # task, so it shares the one fan-in counter and the one hop-depth
        # ledger, not a fresh one.
        h2 = orch._record_handoff(agents, continuation,
                                  {"to": "ops", "goal": "deploy onto the environment"})
        a2 = handoffs.accept(agents, continuation, h2, decided_by="system:auto-accept")

        assert a2["child"]["trigger"]["taskId"] == coordinator_run["runId"]
        children = agents.query(K.task_pk(coordinator_run["runId"]), sk_prefix="CHILD#")
        assert len(children) == 2
        assert {c["runId"] for c in children} == {a1["child"]["runId"], a2["child"]["runId"]}

    def test_a_second_wave_handoff_to_a_brand_new_agent_still_authorizes(
            self, agents, coordinator_run):
        """The harder case: wave two hands off to an agent that was never
        part of wave one, proposed by the continuation run wave one's
        completion created. `collab.send`'s authorization must see this
        handoff as belonging to the same task as wave one, or a task that
        grows its own roster over time can never actually grow it."""
        agents.put({"pk": K.agent_pk(agents.owner_id, "clo"), "sk": "META", "entity": "Agent", **THIRD})
        h1 = orch._record_handoff(agents, coordinator_run,
                                  {"to": "ops", "goal": "provision the environment"})
        a1 = handoffs.accept(agents, coordinator_run, h1, decided_by="system:auto-accept")
        continuation = handoffs.notify_coordinator_if_child(
            agents, a1["child"], RunState.COMPLETED.value, "environment ready")

        h2 = orch._record_handoff(agents, continuation,
                                  {"to": "clo", "goal": "review the compliance impact"})
        a2 = handoffs.accept(agents, continuation, h2, decided_by="system:auto-accept")

        assert a2["child"]["agentId"] == "clo"
        assert a2["handoff"]["status"] == "accepted"
