"""Agent creation and inheritance boundaries.

docs/architecture/17-message-and-memory-authorization.md §4: an agent-created
seat requires the existing human approval decision, inherits nothing from
its parent by default, and no agent can delete another agent. Nothing new is
introduced here -- these tests confirm invariants `agents.py`/`orchestrator.
py`/`api.py` already hold structurally, and would regress loudly if that
ever changed.
"""

import pytest

from amazai import agents as A, approvals, keys as K, runs
from amazai.policy import Capability
from amazai.states import RunState

import handlers.orchestrator as orch
from tests.test_agents_api import NEW_AGENT, api_table, call  # noqa: F401


class TestRequiresApproval:
    def test_an_agent_actor_cannot_call_plan_create_directly(self):
        """`agents.plan_create` is the only path that ever writes an Agent
        row; it refuses outright if the actor carries an `agent_id`."""
        actor = A.Actor(user_id="u", org_id="org-1", agent_id="engineering")
        with pytest.raises(A.Escalation):
            A.plan_create({"name": "Shadow Agent", "role": "x",
                          "modelTier": "balanced"}, actor)

    def test_propose_agent_only_ever_produces_a_pending_approval(self, store):
        """The orchestrator's `propose_agent` tool never writes an Agent row
        itself -- it stops at a `pause: True` approval card."""
        run = runs.create(store, agent_id="engineering", thread_id="dm-engineering",
                          goal="spin up a companion")
        run = runs.advance(store, run, RunState.PLANNING)
        run = runs.advance(store, run, RunState.EXECUTING)

        class _Parsed:
            tool_name = "propose_agent"
            tool_input = {"name": "Shadow Agent", "role": "Handles a bounded lane."}
            tool_use_id = "tu_1"

        result = orch._handle_tool(
            store, run, {"agentId": "engineering"}, _NullEvents(), _NullPush(),
            None, _Parsed(), 1, None)
        assert result["pause"] is True
        assert "approval" in result
        assert store.query_index("gsi1", "gsi1pk", "AGENTS") == []

    def test_the_console_route_only_creates_after_a_human_approves(self, api_table, store):
        call("POST", "/agents", NEW_AGENT)
        run = runs.create(store, agent_id="cloud-operations", thread_id="dm-cloud-operations",
                          goal="propose a companion")
        run = runs.advance(store, run, RunState.PLANNING)
        run = runs.advance(store, run, RunState.EXECUTING)
        proposal = {"name": "Shadow Agent", "role": "Handles a bounded lane.", "description": "x",
                   "systemPrompt": "", "modelTier": "balanced", "workingStyle": "collaborative",
                   "avatar": {"shape": "hex", "color": "#8b5cf6"},
                   "parentAgentId": "cloud-operations", "tools": [], "grants": [],
                   "budget": {"perRunUsd": 0.5, "perMonthUsd": 5.0, "maxConcurrentRuns": 1,
                             "maxToolCallsPerRun": 20, "onCeiling": "hard_stop"}}
        apv = approvals.request(store, run, action="agent.create", arguments=proposal,
                                why="x", capability=Capability.ADMIN)
        runs.pause_for_approval(store, run, apv)

        status, result = call(
            "POST", f"/approvals/{run['runId']}/{apv['approvalId']}", {"approve": True})
        assert status == 200
        assert result["createdAgent"]["status"] == "active"
        assert result["createdAgent"]["parentAgentId"] == "cloud-operations"


class TestNoInheritance:
    def test_a_child_agent_gets_no_grants_by_default(self, store):
        proposal = orch._agent_creation_proposal(
            {"name": "Shadow Agent", "role": "Handles a bounded lane."}, parent_agent_id="engineering")
        actor = A.Actor(user_id="user-1", org_id="org-1")
        plan = A.plan_create(proposal, actor)
        assert plan.agent["allowedTools"] == ["shell", "file_operations"]  # builtins only
        grant_rows = [i for i in plan.items if i["entity"] == "Grant"]
        assert grant_rows == []

    def test_a_child_agent_gets_no_routines_by_default(self, store):
        proposal = orch._agent_creation_proposal(
            {"name": "Shadow Agent", "role": "Handles a bounded lane."}, parent_agent_id="engineering")
        actor = A.Actor(user_id="user-1", org_id="org-1")
        plan = A.plan_create(proposal, actor)
        routine_rows = [i for i in plan.items if i["entity"] == "Routine"]
        assert routine_rows == []

    def test_a_child_agent_gets_no_execution_identity_until_provisioned(self):
        proposal = orch._agent_creation_proposal(
            {"name": "Shadow Agent", "role": "Handles a bounded lane."}, parent_agent_id="engineering")
        actor = A.Actor(user_id="user-1", org_id="org-1")
        plan = A.plan_create(proposal, actor)
        assert plan.agent["harnessArn"] is None
        assert plan.agent["executionRoleArn"] is None
        assert plan.agent["status"] == "provisioning"

    def test_a_child_agent_gets_its_own_empty_memory_namespace(self):
        proposal = orch._agent_creation_proposal(
            {"name": "Shadow Agent", "role": "Handles a bounded lane."}, parent_agent_id="engineering")
        actor = A.Actor(user_id="user-1", org_id="org-1")
        plan = A.plan_create(proposal, actor)
        memns = [i for i in plan.items if i["entity"] == "MemoryNamespace"]
        assert len(memns) == 1
        assert memns[0]["entries"] == 0
        # The child's namespace is its own path, not a copy of the parent's.
        assert memns[0]["namespace"] == f"agents/{plan.agent_id}/memory"

    def test_a_child_agent_starts_with_no_shared_memory_visible_by_inheritance(self, store):
        """Shared `shared_user` memory is org-wide by scope, not parent-
        inherited -- a fresh child sees exactly what any other agent in the
        org would see, nothing extra from its parent."""
        from amazai import memory
        store.put(memory.plan_write(
            {"title": "Parent secret", "body": "only for engineering", "kind": "note"},
            K.agent_pk("engineering"), scope="agent", source="user", author="user-1"))
        proposal = orch._agent_creation_proposal(
            {"name": "Shadow Agent", "role": "Handles a bounded lane."}, parent_agent_id="engineering")
        actor = A.Actor(user_id="user-1", org_id="org-1")
        plan = A.plan_create(proposal, actor)
        store.transact_put(plan.items)

        child_memory = store.query(K.agent_pk(plan.agent_id), sk_prefix="MEM#")
        assert child_memory == []

    def test_the_proposal_normalizer_never_lets_the_model_set_grants_or_tools(self):
        """Even if a malicious model tries to smuggle grants/tools/a bigger
        budget into its `propose_agent` call, the normalizer ignores it --
        it rebuilds the fixed shape from scratch and only reads name/role/
        description/systemPrompt/modelTier/workingStyle/avatar."""
        proposal = orch._agent_creation_proposal(
            {"name": "Shadow Agent", "role": "x",
             "grants": [{"connectorId": "aws", "scope": "admin"}],
             "tools": ["browser", "code_interpreter"],
             "budget": {"perMonthUsd": 999999}},
            parent_agent_id="engineering")
        assert proposal["grants"] == []
        assert proposal["tools"] == []
        assert proposal["budget"]["perMonthUsd"] == 5.00


class TestCannotDeleteAnotherAgent:
    def test_api_actor_is_always_human_never_an_agent(self, api_table):
        """`api._actor` is the only place a DELETE /agents/{id} request is
        authorized, and it is structurally incapable of returning an agent
        actor -- every request into this Lambda came through Auth0."""
        import handlers.api as apimod
        event = {
            "requestContext": {
                "http": {"method": "DELETE"},
                "authorizer": {"jwt": {"claims": {"sub": "owner-a", "custom:orgId": "org-1"}}},
            },
            "rawPath": "/agents/whoever", "headers": {}, "queryStringParameters": None,
            "body": None,
        }
        actor = apimod._actor(event)
        assert actor.is_agent is False
        assert actor.agent_id is None

    def test_no_inline_tool_lets_an_agent_delete_a_peer(self):
        """`INLINE_TOOLS` -- the only surface a running agent can act
        through -- has no delete/remove-agent capability at all."""
        from amazai import agentcore
        assert not any("delete" in name or "remove" in name
                      for name in agentcore.INLINE_TOOLS)

    def test_deleting_an_agent_actually_removes_it(self, api_table):
        call("POST", "/agents", NEW_AGENT)
        status, archived = call("DELETE", "/agents/cloud-operations")
        assert status == 200
        assert archived["status"] == "archived"
        status, listing = call("GET", "/agents")
        assert status == 200
        assert listing["agents"] == []


class _NullEvents:
    def action(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


class _NullPush:
    def tool(self, *a, **k):
        pass

    def handoff(self, *a, **k):
        pass
