"""Max delegation depth on the handoff/child-run tree.

`collab`'s hop-depth guards a *message reply chain*; nothing guarded the
*delegation tree* a handoff builds (CEO -> Marketing -> Research -> ...), so
one operator message could nest delegation without bound. This is the
regression guard for `handoffs.MAX_DELEGATION_DEPTH`: a child run carries its
depth, `can_auto_accept` refuses a too-deep handoff (leaving it `proposed`
for a human, like every other refusal), `accept` raises rather than spawning,
and the fan-in continuation carries the batch's depth forward so post-synthesis
fan-out stays bounded. See `amazai/handoffs.py`.
"""

from __future__ import annotations

import pytest

from amazai import handoffs, keys as K, runs
from amazai.states import RunState

import handlers.orchestrator as orch

COORDINATOR = {"agentId": "eng", "name": "Engineering", "status": "active",
              "budget": {"maxConcurrentRuns": 6}}
RECEIVER = {"agentId": "ops", "name": "Cloud Operations", "status": "active",
           "budget": {"maxConcurrentRuns": 6}}


@pytest.fixture
def agents(store):
    store.put({"pk": K.agent_pk(store.owner_id, "eng"), "sk": "META", "entity": "Agent", **COORDINATOR})
    store.put({"pk": K.agent_pk(store.owner_id, "ops"), "sk": "META", "entity": "Agent", **RECEIVER})
    return store


def _run_at_depth(store, depth: int) -> dict:
    """A coordinator run whose trigger claims it already sits `depth` handoffs
    deep -- the shape `accept`/`notify_coordinator_if_child` produce."""
    # No taskId in the trigger, so the run is its own task -- `accept` binds
    # its delivery message to a real run row (this one) rather than a
    # non-existent task id.
    return runs.create(store, agent_id="eng", thread_id="room-1", goal="ship it",
                       trigger={"type": "child_completion", "delegationDepth": depth})


class TestChildCarriesDepth:
    def test_a_root_run_has_depth_zero_and_its_child_has_depth_one(self, agents):
        root = runs.create(agents, agent_id="eng", thread_id="room-1", goal="ship it")
        assert handoffs._depth_of(root) == 0

        h = orch._record_handoff(agents, root, {"to": "ops", "goal": "do the thing"})
        accepted = handoffs.accept(agents, root, h, decided_by="system:auto-accept")

        assert accepted["child"]["trigger"]["delegationDepth"] == 1
        child_row = agents.get(K.task_pk(root["runId"]), K.task_child_sk(accepted["child"]["runId"]))
        assert child_row["delegationDepth"] == 1

    def test_depth_increments_by_one_per_level(self, agents):
        parent = _run_at_depth(agents, 3)
        h = orch._record_handoff(agents, parent, {"to": "ops", "goal": "next level"})
        accepted = handoffs.accept(agents, parent, h, decided_by="system:auto-accept")
        assert accepted["child"]["trigger"]["delegationDepth"] == 4


class TestCeilingRefusesAutoAccept:
    def test_a_handoff_below_the_ceiling_still_auto_accepts(self, agents):
        parent = _run_at_depth(agents, handoffs.MAX_DELEGATION_DEPTH - 1)
        h = orch._record_handoff(agents, parent, {"to": "ops", "goal": "one more"})
        receiver = agents.get(K.agent_pk(agents.owner_id, "ops"), "META")
        ok, reason = handoffs.can_auto_accept(agents, h, receiver, parent)
        assert ok, reason

    def test_a_handoff_at_the_ceiling_is_not_auto_accepted(self, agents):
        parent = _run_at_depth(agents, handoffs.MAX_DELEGATION_DEPTH)
        h = orch._record_handoff(agents, parent, {"to": "ops", "goal": "too deep"})
        receiver = agents.get(K.agent_pk(agents.owner_id, "ops"), "META")
        ok, reason = handoffs.can_auto_accept(agents, h, receiver, parent)
        assert not ok
        assert "levels deep" in reason

    def test_the_tool_path_leaves_a_too_deep_handoff_proposed_for_a_human(self, agents, monkeypatch):
        """Through the real dispatch: a too-deep handoff must fall back to
        `proposed` (a human decides), never silently spawn -- the same shape
        every other `can_auto_accept` refusal produces."""
        import types
        woken = []
        monkeypatch.setattr(orch, "_invoke_orchestrator_async",
                            lambda run_id, owner: woken.append(run_id))
        parent = _run_at_depth(agents, handoffs.MAX_DELEGATION_DEPTH)
        agent = agents.get(K.agent_pk(agents.owner_id, "eng"), "META")

        from amazai.cost import RunCost
        from amazai.evidence import EvidenceWriter
        from amazai.push import Push

        class _Push(Push):
            def __init__(self):
                self.sent = []

            def send(self, payload):
                self.sent.append(payload)
                return 0

        parsed = types.SimpleNamespace(tool_name="handoff",
                                       tool_input={"to": "ops", "goal": "too deep"},
                                       tool_use_id="tu-1")
        result = orch._handle_tool(agents, parent, agent, EvidenceWriter(parent["runId"]),
                                   _Push(), resolution=None, parsed=parsed, seq=1,
                                   cost=RunCost(), turn=orch.Turn())
        assert result["toolResult"]["status"] == "proposed"
        assert woken == []


class TestAcceptRaisesAtTheCeiling:
    def test_manual_accept_raises_rather_than_spawning_a_child(self, agents):
        """`accept` is the single choke point the manual `POST /handoffs` path
        also goes through, so the ceiling has to hold there even when
        `can_auto_accept` was never consulted."""
        parent = _run_at_depth(agents, handoffs.MAX_DELEGATION_DEPTH)
        h = orch._record_handoff(agents, parent, {"to": "ops", "goal": "too deep"})
        with pytest.raises(handoffs.HandoffError, match="levels deep"):
            handoffs.accept(agents, parent, h, decided_by="user:someone")
        # No child spawned, no task counter touched.
        assert agents.query(K.task_pk(parent["runId"]), sk_prefix="CHILD#") == []

    def test_accept_one_below_the_ceiling_still_spawns(self, agents):
        parent = _run_at_depth(agents, handoffs.MAX_DELEGATION_DEPTH - 1)
        h = orch._record_handoff(agents, parent, {"to": "ops", "goal": "last allowed level"})
        accepted = handoffs.accept(agents, parent, h, decided_by="user:someone")
        assert accepted["child"]["trigger"]["delegationDepth"] == handoffs.MAX_DELEGATION_DEPTH


class TestContinuationCarriesDepthForward:
    def test_the_synthesis_continuation_keeps_the_batch_depth(self, agents):
        """A coordinator that fans out again from the fan-in continuation must
        still be counted from where the batch left off, not reset to zero."""
        parent = _run_at_depth(agents, 2)
        h = orch._record_handoff(agents, parent, {"to": "ops", "goal": "task one"})
        accepted = handoffs.accept(agents, parent, h, decided_by="system:auto-accept")
        assert accepted["child"]["trigger"]["delegationDepth"] == 3

        continuation = handoffs.notify_coordinator_if_child(
            agents, accepted["child"], RunState.COMPLETED.value, "done")
        assert continuation is not None
        # The child sat at depth 3; the continuation resumes at that level so a
        # fresh handoff from synthesis is a genuinely deeper level 4.
        assert continuation["trigger"]["delegationDepth"] == 3
