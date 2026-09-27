"""Deferred agent-to-agent messages get drained, exactly once.

`orchestrator._message_agent` only wakes a recipient on the priority path; a
`priority: false` message was persisted as a durable `AgentMessage` and then
nothing ever picked it up, so non-urgent Companion-to-Companion work stranded
forever. This is the regression guard for the fix: `collab.send` drops a
durable `PendingWake` marker for a non-priority message (and none for a
priority one), and the recovery sweep (`handlers.sweeper`) drains each marker
into exactly one recipient run within the same `may_wake_now` concurrency
budget the priority path respects. See `amazai/collab.py`,
`handlers/sweeper.py`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from amazai import collab, keys as K, runs
from amazai.push import Push

import handlers.sweeper as sweeper

SENDER = {"agentId": "eng", "name": "Engineering", "status": "active",
          "budget": {"maxConcurrentRuns": 6}}
RECIPIENT = {"agentId": "ops", "name": "Cloud Operations", "status": "active",
             "budget": {"maxConcurrentRuns": 3}}


@pytest.fixture
def agents(store):
    store.put({"pk": K.agent_pk(store.owner_id, "eng"), "sk": "META", "entity": "Agent", **SENDER})
    store.put({"pk": K.agent_pk(store.owner_id, "ops"), "sk": "META", "entity": "Agent", **RECIPIENT})
    return store


@pytest.fixture
def room(agents):
    """A room both agents are in, so a message binds without a task."""
    thread_id = "room-collab"
    agents.put({"pk": K.thread_pk(agents.owner_id, thread_id), "sk": "META",
                "entity": "Thread", "threadId": thread_id, "kind": "room",
                "agentIds": ["eng", "ops"], "status": "active"})
    return thread_id


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


def _pending_wakes(store):
    return [r for r in store.query_index("gsi1", "gsi1pk", K.PENDING_WAKES_GSI1PK, limit=50)
            if r.get("entity") == "PendingWake"]


def _recipient_runs(store, agent_id="ops"):
    return [r for r in store.query_index("gsi1", "gsi1pk", "RUNS", limit=100)
            if r.get("agentId") == agent_id]


def _send_deferred(store, room, *, text="please review when free"):
    return collab.send(store, sender_agent_id="eng", recipient_agent_id="ops",
                       args={"text": text, "collaboration_context_id": room})


class TestMarkerOnSend:
    def test_a_deferred_message_writes_a_marker_and_wakes_no_one(self, agents, room):
        outcome = _send_deferred(agents, room)
        assert outcome["priority_granted"] is False

        markers = _pending_wakes(agents)
        assert len(markers) == 1
        marker = markers[0]
        assert marker["status"] == "pending"
        assert marker["recipientAgentId"] == "ops"
        assert marker["threadId"] == room
        assert marker["messageId"] == outcome["message"]["messageId"]

        # The whole point: nothing woke the recipient at send time.
        assert _recipient_runs(agents) == []

    def test_a_priority_granted_message_writes_no_marker(self, agents, room):
        outcome = collab.send(agents, sender_agent_id="eng", recipient_agent_id="ops",
                              args={"text": "urgent", "priority": True,
                                    "collaboration_context_id": room})
        assert outcome["priority_granted"] is True
        assert _pending_wakes(agents) == []


class TestSweepDrains:
    def test_a_sweep_drains_a_deferred_message_into_one_recipient_run(
            self, agents, room, monkeypatch):
        fake = _stub_lambda(monkeypatch)
        _send_deferred(agents, room)

        result = sweeper._sweep_owner(agents, Push(agents), datetime.now(timezone.utc))

        assert result["deferredWakesDrained"] == 1
        runs_after = _recipient_runs(agents)
        assert len(runs_after) == 1
        run = runs_after[0]
        assert run["trigger"]["type"] == "agent"
        assert run["trigger"]["fromAgentId"] == "eng"
        assert run["threadId"] == room
        assert run["goal"] == "please review when free"

        marker = _pending_wakes(agents)[0]
        assert marker["status"] == "delivered"
        assert marker["runId"] == run["runId"]

        # The recipient's run was actually invoked, like the priority path.
        assert any(run["runId"].encode() in inv["Payload"] for inv in fake.invocations)

    def test_a_second_sweep_creates_no_duplicate_run(self, agents, room, monkeypatch):
        _stub_lambda(monkeypatch)
        _send_deferred(agents, room)

        first = sweeper._sweep_owner(agents, Push(agents), datetime.now(timezone.utc))
        second = sweeper._sweep_owner(agents, Push(agents), datetime.now(timezone.utc))

        assert first["deferredWakesDrained"] == 1
        assert second["deferredWakesDrained"] == 0
        assert len(_recipient_runs(agents)) == 1


class TestConcurrencyCeiling:
    def test_a_recipient_at_its_ceiling_is_left_pending_then_drained_later(
            self, agents, room, monkeypatch):
        _stub_lambda(monkeypatch)
        # ops budget is 3; fill it so may_wake_now refuses.
        busy = [runs.create(agents, agent_id="ops", thread_id=f"dm-{i}", goal="busy")
                for i in range(3)]
        _send_deferred(agents, room)

        blocked = sweeper._sweep_owner(agents, Push(agents), datetime.now(timezone.utc))
        assert blocked["deferredWakesDrained"] == 0
        marker = _pending_wakes(agents)[0]
        assert marker["status"] == "pending"  # released, not dropped

        # Capacity frees, and a later sweep drains it.
        from amazai.states import RunState
        for r in busy:
            runs.advance(agents, r, RunState.COMPLETED)
        drained = sweeper._sweep_owner(agents, Push(agents), datetime.now(timezone.utc))
        assert drained["deferredWakesDrained"] == 1
        assert _pending_wakes(agents)[0]["status"] == "delivered"

    def test_a_marker_for_a_vanished_recipient_is_dropped(self, agents, room, monkeypatch):
        _stub_lambda(monkeypatch)
        _send_deferred(agents, room)
        agents.update(K.agent_pk(agents.owner_id, "ops"), "META", {"status": "archived"})

        result = sweeper._sweep_owner(agents, Push(agents), datetime.now(timezone.utc))
        assert result["deferredWakesDrained"] == 0
        assert _pending_wakes(agents)[0]["status"] == "dropped"
        assert _recipient_runs(agents) == []
