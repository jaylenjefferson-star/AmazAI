"""Child-run isolation and task lifecycle.

A handoff's child run used to execute inside the *coordinator's own* thread:
same session-id inputs as the coordinator (when the receiving agent is also
the coordinator's own thread's other party) and, worse, the same session as
any other child handed to the same receiving agent from the same coordinator
thread. It also meant a child's `_drive` history load pulled in whatever the
coordinator's own conversation happened to contain, not just the brief.

These tests pin the fix: a handoff gets its own dedicated thread
(`keys.handoff_child_thread_id`) for the child's own execution, and the Task
row now has a real status lifecycle plus a gsi1 listing.
"""

from __future__ import annotations

import pytest

from amazai import handoffs, keys as K, runs
from amazai.states import RunState
from amazai.store import Store

import handlers.orchestrator as orch

COORDINATOR = {"agentId": "eng", "name": "Engineering", "status": "active",
              "budget": {"maxConcurrentRuns": 5}}
FINANCE = {"agentId": "fin", "name": "Finance", "status": "active",
          "budget": {"maxConcurrentRuns": 5}}
MARKETING = {"agentId": "mkt", "name": "Marketing", "status": "active",
            "budget": {"maxConcurrentRuns": 5}}


@pytest.fixture
def agents(store):
    store.put({"pk": K.agent_pk(store.owner_id, "eng"), "sk": "META", "entity": "Agent", **COORDINATOR})
    store.put({"pk": K.agent_pk(store.owner_id, "fin"), "sk": "META", "entity": "Agent", **FINANCE})
    store.put({"pk": K.agent_pk(store.owner_id, "mkt"), "sk": "META", "entity": "Agent", **MARKETING})
    return store


@pytest.fixture
def coordinator_run(agents):
    return runs.create(agents, agent_id="eng", thread_id="dm-eng", goal="evaluate a new launch")


class TestChildThreadIsolation:
    def test_child_run_does_not_execute_in_the_coordinators_own_thread(
            self, agents, coordinator_run):
        handoff = orch._record_handoff(agents, coordinator_run,
                                       {"to": "fin", "goal": "financial model"})
        accepted = handoffs.accept(agents, coordinator_run, handoff,
                                   decided_by="system:auto-accept")

        assert accepted["child"]["threadId"] != coordinator_run["threadId"]
        assert accepted["child"]["threadId"] == K.handoff_child_thread_id(handoff["handoffId"])

    def test_the_delivery_message_still_lands_in_the_coordinators_thread(
            self, agents, coordinator_run):
        handoff = orch._record_handoff(agents, coordinator_run,
                                       {"to": "fin", "goal": "financial model"})
        handoffs.accept(agents, coordinator_run, handoff, decided_by="system:auto-accept")

        messages = [r for r in agents.query(
            K.thread_pk(agents.owner_id, coordinator_run["threadId"]), sk_prefix="MSG#")
            if r.get("entity") == "AgentMessage"]
        assert len(messages) == 1

    def test_prior_conversation_in_the_coordinators_thread_is_not_in_the_childs(
            self, agents, coordinator_run):
        """The thing this whole fix is for: a message the operator sent the
        coordinator before the handoff must not show up as history when the
        child agent's own run loads its thread."""
        agents.put({
            "pk": K.thread_pk(agents.owner_id, coordinator_run["threadId"]),
            "sk": K.message_sk("2024-01-01T00:00:00Z", "aaa"),
            "entity": "Message", "role": "user", "author": "owner",
            "text": "here is something private about our Q1 numbers",
            "at": "2024-01-01T00:00:00Z",
        })

        handoff = orch._record_handoff(agents, coordinator_run,
                                       {"to": "fin", "goal": "financial model"})
        accepted = handoffs.accept(agents, coordinator_run, handoff,
                                   decided_by="system:auto-accept")

        child_messages = agents.query(
            K.thread_pk(agents.owner_id, accepted["child"]["threadId"]), sk_prefix="MSG#")
        assert child_messages == []

    def test_two_handoffs_to_the_same_agent_get_different_sessions(
            self, agents, coordinator_run):
        """The collision case: two tasks routed through the same coordinator
        thread, both landing on Finance, used to hash to the identical
        AgentCore session id (owner, agentId, threadId)."""
        h1 = orch._record_handoff(agents, coordinator_run, {"to": "fin", "goal": "task one"})
        a1 = handoffs.accept(agents, coordinator_run, h1, decided_by="system:auto-accept")

        second_coordinator_run = runs.create(
            agents, agent_id="eng", thread_id="dm-eng", goal="a second, unrelated request")
        h2 = orch._record_handoff(agents, second_coordinator_run,
                                  {"to": "fin", "goal": "task two"})
        a2 = handoffs.accept(agents, second_coordinator_run, h2, decided_by="system:auto-accept")

        assert a1["child"]["sessionId"] != a2["child"]["sessionId"]
        assert a1["child"]["threadId"] != a2["child"]["threadId"]


class TestTaskLifecycle:
    def test_task_is_listed_open_while_a_child_is_outstanding(self, agents, coordinator_run):
        handoff = orch._record_handoff(agents, coordinator_run,
                                       {"to": "fin", "goal": "financial model"})
        handoffs.accept(agents, coordinator_run, handoff, decided_by="system:auto-accept")

        rows = agents.query_index("gsi1", "gsi1pk", "TASKS", limit=50)
        assert len(rows) == 1
        assert rows[0]["status"] == "open"
        assert rows[0]["taskId"] == coordinator_run["runId"]

    def test_task_moves_to_awaiting_synthesis_when_the_last_child_reports(
            self, agents, coordinator_run):
        handoff = orch._record_handoff(agents, coordinator_run,
                                       {"to": "fin", "goal": "financial model"})
        accepted = handoffs.accept(agents, coordinator_run, handoff, decided_by="system:auto-accept")

        handoffs.notify_coordinator_if_child(
            agents, accepted["child"], RunState.COMPLETED.value, "model built")

        task = agents.get(K.task_pk(coordinator_run["runId"]), "META")
        assert task["status"] == "awaiting_synthesis"
        rows = agents.query_index("gsi1", "gsi1pk", "TASKS", limit=50)
        assert rows[0]["status"] == "awaiting_synthesis"

    def test_task_closes_once_the_synthesis_run_settles_with_nothing_reopened(
            self, agents, coordinator_run):
        handoff = orch._record_handoff(agents, coordinator_run,
                                       {"to": "fin", "goal": "financial model"})
        accepted = handoffs.accept(agents, coordinator_run, handoff, decided_by="system:auto-accept")
        continuation = handoffs.notify_coordinator_if_child(
            agents, accepted["child"], RunState.COMPLETED.value, "model built")

        handoffs.close_task_if_finished(agents, continuation, RunState.COMPLETED.value)

        task = agents.get(K.task_pk(coordinator_run["runId"]), "META")
        assert task["status"] == "closed"
        assert task["outcome"] == "completed"
        rows = agents.query_index("gsi1", "gsi1pk", "TASKS", limit=50)
        assert rows[0]["status"] == "closed"

    def test_a_coordinator_settling_mid_fan_out_does_not_close_the_task(
            self, agents, coordinator_run):
        """The original coordinator run finishes its own turn ("I've handed
        this off") while a child is still outstanding -- that must not be
        mistaken for the task being done."""
        handoff = orch._record_handoff(agents, coordinator_run,
                                       {"to": "fin", "goal": "financial model"})
        handoffs.accept(agents, coordinator_run, handoff, decided_by="system:auto-accept")

        handoffs.close_task_if_finished(agents, coordinator_run, RunState.COMPLETED.value)

        task = agents.get(K.task_pk(coordinator_run["runId"]), "META")
        assert task["status"] == "open"

    def test_a_stale_coordinator_settling_after_a_continuation_already_took_over_is_ignored(
            self, agents, coordinator_run):
        """A race: the child finished, the continuation was created and even
        already settled the task, and only then does the *original*
        coordinator run's own settle land. It must not re-close (or reopen)
        a task a later run now owns."""
        handoff = orch._record_handoff(agents, coordinator_run,
                                       {"to": "fin", "goal": "financial model"})
        accepted = handoffs.accept(agents, coordinator_run, handoff, decided_by="system:auto-accept")
        continuation = handoffs.notify_coordinator_if_child(
            agents, accepted["child"], RunState.COMPLETED.value, "model built")
        handoffs.close_task_if_finished(agents, continuation, RunState.COMPLETED.value)

        # The original run's own (delayed) settle arrives last.
        handoffs.close_task_if_finished(agents, coordinator_run, RunState.COMPLETED.value)

        task = agents.get(K.task_pk(coordinator_run["runId"]), "META")
        assert task["status"] == "closed"
        assert task["outcome"] == "completed"

    def test_a_failed_only_child_closes_the_task_as_failed(self, agents, coordinator_run):
        handoff = orch._record_handoff(agents, coordinator_run,
                                       {"to": "fin", "goal": "financial model"})
        accepted = handoffs.accept(agents, coordinator_run, handoff, decided_by="system:auto-accept")
        continuation = handoffs.notify_coordinator_if_child(
            agents, accepted["child"], RunState.FAILED.value, "ran out of data")

        handoffs.close_task_if_finished(agents, continuation, RunState.FAILED.value)

        task = agents.get(K.task_pk(coordinator_run["runId"]), "META")
        assert task["status"] == "closed"
        assert task["outcome"] == "failed"
