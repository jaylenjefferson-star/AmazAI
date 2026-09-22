"""Agent creation and inheritance boundaries.

docs/architecture/17-message-and-memory-authorization.md §4: an agent-created
seat requires the existing human approval decision, and no agent can delete
another agent. These tests confirm invariants `agents.py`/`orchestrator.py`/
`api.py` hold structurally, and would regress loudly if that ever changed.

One exception, tested in `test_bots_create_bots.py`: when the operator's own
message started the run, a Bot may create a Bot at once. Everything below is about
every *other* way a Bot could try, which still ends in a card a person approves.
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

    @pytest.mark.parametrize("trigger", [
        {"type": "routine"},                                   # a schedule fired it
        {"type": "agent", "fromAgentId": "cloud-operations"},  # a teammate woke it
    ])
    def test_a_bot_acting_on_its_own_only_ever_produces_a_pending_approval(self, store, trigger):
        """When the operator's own message did not start the run, the orchestrator's
        `propose_agent` tool never writes an Agent row itself -- it stops at a
        `pause: True` approval card."""
        run = runs.create(store, agent_id="engineering", thread_id="dm-engineering",
                          goal="spin up a companion", trigger=trigger)
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
                   "avatar": {"shape": "moth", "color": "#8b5cf6"},
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
            K.agent_pk(store.owner_id, "engineering"), scope="agent", source="user", author="user-1"))
        proposal = orch._agent_creation_proposal(
            {"name": "Shadow Agent", "role": "Handles a bounded lane."}, parent_agent_id="engineering")
        actor = A.Actor(user_id="user-1", org_id="org-1")
        plan = A.plan_create(proposal, actor)
        store.transact_put(plan.items)

        child_memory = store.query(K.agent_pk(store.owner_id, plan.agent_id), sk_prefix="MEM#")
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


class TestApprovingMakesTheAgentUsable:
    """An owner who approves an agent another agent proposed gets a teammate that
    can use what they have connected, not an empty seat. The proposal itself still
    carries no grants (a model cannot ask for any); the owner's own approval is what
    applies the owner's connected apps."""

    def _approve(self, store, api_table, monkeypatch, parent_capability=None):
        from tests.fake_composio import FakeTransport, wire
        wire(monkeypatch, FakeTransport(connected={"slack"}))
        call("POST", "/agents", NEW_AGENT)
        call("POST", "/connectors/composio:slack/install", {})
        if parent_capability:
            call("PUT", "/agents/cloud-operations/grants/composio:slack", {"capability": parent_capability})
        run = runs.create(store, agent_id="cloud-operations", thread_id="dm-cloud-operations", goal="x")
        run = runs.advance(store, run, RunState.PLANNING)
        run = runs.advance(store, run, RunState.EXECUTING)
        proposal = orch._agent_creation_proposal(
            {"name": "Shadow Agent", "role": "Handles a bounded lane."}, parent_agent_id="cloud-operations")
        assert proposal["grants"] == [], "a model must never be able to ask for grants"
        apv = approvals.request(store, run, action="agent.create", arguments=proposal,
                                why="x", capability=Capability.ADMIN)
        runs.pause_for_approval(store, run, apv)
        status, result = call("POST", f"/approvals/{run['runId']}/{apv['approvalId']}", {"approve": True})
        assert status == 200, result
        return result["createdAgent"]["agentId"]

    def test_the_approved_agent_starts_with_the_apps_the_owner_connected(self, store, api_table, monkeypatch):
        from amazai import connectors as C
        child = self._approve(store, api_table, monkeypatch)
        assert [g.slug for g in C.granted_apps(store, child)] == ["slack"]

    def test_it_does_not_inherit_a_read_only_parent_limit_or_anything_else_from_it(self, store, api_table, monkeypatch):
        from amazai import connectors as C
        from amazai.policy import Capability as Cap
        child = self._approve(store, api_table, monkeypatch, parent_capability="read")
        [parent] = C.granted_apps(store, "cloud-operations")
        assert parent.capability is Cap.READ
        [held] = C.granted_apps(store, child)
        assert held.capability is Cap.ADMIN, "the child got the parent's limit instead of the owner's default"

    def test_and_a_write_by_the_new_agent_still_asks(self, store, api_table, monkeypatch):
        # Openness is about reach, not about skipping confirmation.
        from amazai import connectors as C
        from amazai.policy import Capability as Cap, evaluate
        child = self._approve(store, api_table, monkeypatch)
        assert C.granted_apps(store, child)
        assert evaluate("SLACK_SEND_MESSAGE", Cap.WRITE).required is True



class TestApprovedCreationAlwaysResumesTheProposer:
    def setup_approval(self, api_table, *, first_task="Summarise the rivals."):
        from amazai.store import Store

        call("POST", "/agents", NEW_AGENT)
        store = Store("owner-a", table=api_table)
        store.update(K.agent_pk(store.owner_id, "cloud-operations"), "META",
                     {"model": {"modelId": "resolved-model", "tier": "balanced"}})
        run = runs.create(store, agent_id="cloud-operations", thread_id="dm-cloud-operations",
                          goal="propose a companion", trigger={"type": "routine"})
        run = runs.advance(store, run, RunState.PLANNING)
        run = runs.advance(store, run, RunState.EXECUTING)
        proposal = orch._agent_creation_proposal({
            "name": "Shadow Agent", "role": "Handles a bounded lane.",
            "description": "Owns a bounded lane.", "firstTask": first_task,
        }, parent_agent_id="cloud-operations")
        apv = approvals.request(store, run, action="agent.create", arguments=proposal,
                                why="x", capability=Capability.ADMIN)
        run = runs.pause_for_approval(store, run, apv)
        return store, run, apv

    def test_a_deferred_first_task_is_reported_and_the_parent_resumes(self, api_table, monkeypatch):
        import handlers.api as api
        store, run, apv = self.setup_approval(api_table)
        monkeypatch.setattr(api.collab, "may_wake_now",
                            lambda store, bot, limits: (False, "over budget"))
        resumed = []
        monkeypatch.setattr(api, "_invoke_orchestrator",
                            lambda run_id, owner_id, **kw: resumed.append((run_id, kw)))

        status, result = call(
            "POST", f"/approvals/{run['runId']}/{apv['approvalId']}", {"approve": True})

        assert status == 200 and result["createdAgent"]["status"] == "active"
        assert result["firstTask"] == {"status": "deferred", "reason": "over budget"}
        assert result["approval"]["executionStatus"] == "created"
        assert result["approval"]["firstTaskStatus"] == result["firstTask"]
        assert store.get(run["pk"], "META")["state"] == RunState.EXECUTING.value
        assert resumed and resumed[-1][0] == run["runId"] and resumed[-1][1]["resume"] is True
        saved = store.get(run["pk"], K.approval_sk(apv["approvalId"]))
        assert saved["executionStatus"] == "created"
        assert saved["firstTaskStatus"]["status"] == "deferred"
        events = store.query(K.thread_pk(store.owner_id, run["threadId"]), sk_prefix="MSG#")
        assert any("first task waiting: over budget" in e.get("text", "") for e in events)

    def test_a_creation_failure_is_recorded_and_the_parent_still_resumes(self, api_table, monkeypatch):
        import handlers.api as api
        store, run, apv = self.setup_approval(api_table)
        monkeypatch.setattr(api, "_create_approved_agent",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("provision failed")))
        resumed = []
        monkeypatch.setattr(api, "_invoke_orchestrator",
                            lambda run_id, owner_id, **kw: resumed.append((run_id, kw)))

        status, result = call(
            "POST", f"/approvals/{run['runId']}/{apv['approvalId']}", {"approve": True})

        assert status == 200 and "provision failed" in result["creationError"]
        assert result["approval"]["executionStatus"] == "failed"
        assert "provision failed" in result["approval"]["executionError"]
        assert store.get(run["pk"], "META")["state"] == RunState.EXECUTING.value
        assert resumed and resumed[-1][0] == run["runId"]
        saved = store.get(run["pk"], K.approval_sk(apv["approvalId"]))
        assert saved["executionStatus"] == "failed"
        assert "provision failed" in saved["executionError"]
        events = store.query(K.thread_pk(store.owner_id, run["threadId"]), sk_prefix="MSG#")
        assert any("Bot creation failed" in e.get("text", "") for e in events)



def test_creation_outcome_history_failure_cannot_strand_the_parent(api_table, monkeypatch):
    import handlers.api as api
    from amazai.store import Store

    call("POST", "/agents", NEW_AGENT)
    store = Store("owner-a", table=api_table)
    store.update(K.agent_pk(store.owner_id, "cloud-operations"), "META",
                 {"model": {"modelId": "resolved-model", "tier": "balanced"}})
    run = runs.create(store, agent_id="cloud-operations", thread_id="dm-cloud-operations",
                      goal="propose a companion", trigger={"type": "routine"})
    run = runs.advance(store, run, RunState.PLANNING)
    run = runs.advance(store, run, RunState.EXECUTING)
    proposal = orch._agent_creation_proposal(
        {"name": "Shadow Agent", "role": "Handles a bounded lane.", "description": "x"},
        parent_agent_id="cloud-operations")
    apv = approvals.request(store, run, action="agent.create", arguments=proposal,
                            why="x", capability=Capability.ADMIN)
    run = runs.pause_for_approval(store, run, apv)
    resumed = []
    monkeypatch.setattr(api.threads, "event",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("history unavailable")))
    monkeypatch.setattr(api, "_invoke_orchestrator",
                        lambda run_id, owner_id, **kw: resumed.append((run_id, kw)))

    status, result = call(
        "POST", f"/approvals/{run['runId']}/{apv['approvalId']}", {"approve": True})

    assert status == 200 and result["createdAgent"]["status"] == "active"
    assert "execution history" in result["creationWarning"]
    assert store.get(run["pk"], "META")["state"] == RunState.EXECUTING.value
    assert resumed and resumed[-1][0] == run["runId"]
