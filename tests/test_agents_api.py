"""The agent routes, driven through the real Lambda handler.

These exist because the interesting failure is not in any one function: it is
what the database looks like after the harness call fails, which only the
handler's two-phase path can produce.
"""

import json
from types import SimpleNamespace

import pytest

from amazai import collab, identity, keys as K
from amazai.store import Store

import handlers.api as api
from amazai import agentcore


def event(method, path, body=None, *, headers=None, qs=None, sub="owner-a"):
    return {
        "requestContext": {
            "http": {"method": method},
            "authorizer": {"jwt": {"claims": {"sub": sub, "custom:orgId": "org-1"}}},
        },
        "rawPath": path,
        "headers": headers or {},
        "queryStringParameters": qs,
        "body": json.dumps(body) if body is not None else None,
    }


def call(method, path, body=None, **kw):
    resp = api.handler(event(method, path, body, **kw), None)
    return resp["statusCode"], json.loads(resp["body"]) if resp.get("body") else None


NEW_AGENT = {
    "name": "Cloud Operations",
    "role": "AWS investigations, logs, alarms.",
    "modelTier": "frontier",
    "avatar": {"shape": "paper", "color": "#2f6fe4"},
    "budget": {"perRunUsd": 1.5, "perMonthUsd": 30.0},
}


@pytest.fixture
def api_table(table, monkeypatch):
    """The handler builds its own Store, so point it at moto's table and give
    every created agent a resolved model — the harness itself is stubbed."""
    monkeypatch.setattr(api, "_provision_harness", lambda store, agent: store.update(
        K.agent_pk(store.owner_id, agent["agentId"]), "META",
        {"harnessArn": "arn:aws:bedrock-agentcore:us-west-2:1:harness/x",
         "status": "active", "state": "active"}))
    # The handler builds `Store(_owner(event))` itself, so replace the name it
    # reaches for rather than the class — patching __init__ would recurse.
    monkeypatch.setattr(api, "Store", lambda owner_id: Store(owner_id, table=table))
    # Route tests exercise the handler's ownership behavior; token crypto is
    # covered independently in test_identity.py.
    def principal_from_event(evt):
        claims = (((evt.get("requestContext") or {}).get("authorizer") or {})
                  .get("jwt") or {}).get("claims") or {}
        return identity.Principal(user_id=claims.get("sub", "owner-a"))
    monkeypatch.setattr(api.identity, "principal_from_event", principal_from_event)
    return table


class TestCreate:
    def test_stack_allows_the_agentcore_runtime_creation_action(self):
        """`create_harness` is authorized by AWS as CreateAgentRuntime.

        Without this exact action a profile passes validation, is rolled back,
        and the console can only say that creation failed.
        """
        from pathlib import Path
        stack = (Path(__file__).resolve().parents[1] / "infra/lib/amazai-stack.ts").read_text()
        assert "'bedrock-agentcore:CreateAgentRuntime'" in stack
        assert "'bedrock-agentcore:CreateAgentRuntimeEndpoint'" in stack

    def test_create_harness_accepts_the_current_nested_arn_response(self):
        class Control:
            def create_harness(self, **kwargs):
                return {"harness": {"arn": "arn:aws:bedrock-agentcore:us-west-2:1:harness/new"}}

        arn = agentcore.AgentCore(runtime=object(), control=Control()).create_harness(
            name="amazai_chief", execution_role_arn="arn:aws:iam::1:role/bot", tool_names=[])
        assert arn == "arn:aws:bedrock-agentcore:us-west-2:1:harness/new"

    def test_dedicated_rollback_passes_the_restricted_agent_role_to_agentcore(self, monkeypatch):
        calls = []

        class Core:
            def create_harness(self, **kwargs):
                calls.append(kwargs)
                return "arn:aws:bedrock-agentcore:us-west-2:1:harness/new"

            def get_harness(self, harness_arn):
                return {"harness": {"status": "READY", "executionRoleArn": role}}

        class StoreStub:
            owner_id = "owner-a"

            def update(self, pk, sk, values):
                return {"agentId": "tanzie", **values}

        role = "arn:aws:iam::1:role/amazai-agent-dynamic"
        monkeypatch.setenv("AGENT_ROLE_ARN", role)
        monkeypatch.setenv("AMAZAI_SHARED_RUNTIME", "false")
        monkeypatch.setattr(api.agentcore, "AgentCore", Core)

        created = api._provision_harness(StoreStub(), {
            "agentId": "tanzie",
            "model": {"tier": "balanced", "modelId": "us.example.model"},
            "allowedTools": ["shell", "file_operations"],
        })

        assert calls == [{"name": "amazai_tanzie", "execution_role_arn": role,
                          "tool_names": ["shell", "file_operations"]}]
        assert created["executionRoleArn"] == role
        assert created["runtimeMode"] == "dedicated"

    def test_new_bot_reuses_an_account_resolved_model(self, api_table):
        status, existing = call("POST", "/agents", NEW_AGENT)
        assert status == 201
        Store("owner-a", table=api_table).update(K.agent_pk(Store("owner-a", table=api_table).owner_id, existing["agentId"]), "META", {
            "model": {"tier": "frontier", "modelId": "us.anthropic.claude-opus-4-6-v1"},
        })

        status, created = call("POST", "/agents", {
            "name": "Chief", "entrypoint": True,
            "avatar": {"shape": "pebble", "color": "#2f6fe4"},
        })
        assert status == 201
        assert created["model"]["modelId"] == "us.anthropic.claude-opus-4-6-v1"

    def test_a_created_agent_is_active_and_listed(self, api_table):
        status, agent = call("POST", "/agents", NEW_AGENT)
        assert status == 201
        assert agent["agentId"] == "cloud-operations"
        assert agent["status"] == "active"

        status, listing = call("GET", "/agents")
        assert [a["agentId"] for a in listing["agents"]] == ["cloud-operations"]

    def test_creation_writes_grants_memory_and_a_starter_thread(self, api_table):
        call("POST", "/agents", NEW_AGENT)
        status, agent = call("GET", "/agents/cloud-operations")
        assert status == 200
        assert agent["memoryNamespace"] == "agents/cloud-operations/memory"
        assert [e["action"] for e in agent["audit"]] == ["agent.created"]

        status, threads = call("GET", "/threads")
        assert "dm-cloud-operations" in [t["threadId"] for t in threads["threads"]]

    def test_a_failed_harness_leaves_nothing_behind(self, api_table, monkeypatch):
        """The guarantee: if provisioning fails, there is no agent — not an
        inert one, not one missing its grants."""
        def boom(store, agent):
            raise RuntimeError("harness did not reach READY")

        monkeypatch.setattr(api, "_provision_harness", boom)

        status, err = call("POST", "/agents", NEW_AGENT)
        assert status == 502
        assert "harness did not reach READY" in err["detail"]

        assert call("GET", "/agents/cloud-operations")[0] == 404
        assert call("GET", "/agents")[1]["agents"] == []

        store = Store("owner-a", table=api_table)
        assert store.query(K.agent_pk(store.owner_id, "cloud-operations"), sk_prefix="GRANT#") == []
        assert store.try_get(K.thread_pk(store.owner_id, "dm-cloud-operations"), "META") is None

    def test_a_failed_attempt_still_leaves_an_audit_trail(self, api_table, monkeypatch):
        """Rollback removes the agent. That it was attempted and why it failed
        is the one thing worth keeping."""
        monkeypatch.setattr(api, "_provision_harness",
                            lambda s, a: (_ for _ in ()).throw(RuntimeError("no model")))
        call("POST", "/agents", NEW_AGENT)

        store = Store("owner-a", table=api_table)
        trail = store.query(K.agent_pk(store.owner_id, "cloud-operations"), sk_prefix="AUDIT#")
        assert [e["action"] for e in trail] == ["agent.provision_failed"]
        assert "no model" in trail[0]["detail"]

    def test_the_same_idempotency_key_returns_the_first_agent(self, api_table):
        headers = {"Idempotency-Key": "abc-123"}
        first = call("POST", "/agents", NEW_AGENT, headers=headers)
        second = call("POST", "/agents", NEW_AGENT, headers=headers)

        assert first[0] == 201
        assert second[0] == 200
        assert second[1]["agentId"] == first[1]["agentId"]
        assert len(call("GET", "/agents")[1]["agents"]) == 1

    def test_a_duplicate_name_without_a_key_is_a_conflict(self, api_table):
        assert call("POST", "/agents", NEW_AGENT)[0] == 201
        assert call("POST", "/agents", NEW_AGENT)[0] == 409

    def test_a_grant_on_an_uninstalled_connector_is_refused(self, api_table):
        status, err = call("POST", "/agents", dict(
            NEW_AGENT, grants=[{"connectorId": "composio:stripe",
                                "allowedTools": ["payment.charge"]}]))
        assert status == 403
        assert "not installed" in err["detail"]
        assert call("GET", "/agents")[1]["agents"] == []

    def test_a_malformed_request_is_rejected_before_anything_is_written(self, api_table):
        status, err = call("POST", "/agents", {"name": "x"})
        assert status == 400
        assert call("GET", "/agents")[1]["agents"] == []


class TestOptions:
    """The Create-a-Bot form is built from this response, so a drift between
    it and the validator shows up as a form offering something the API
    refuses."""

    def test_every_offered_shape_and_colour_is_actually_accepted(self, api_table):
        _, options = call("GET", "/agents/options")

        for shape in options["shapes"]:
            body = dict(NEW_AGENT, name=f"Agent {shape}",
                        avatar={"shape": shape, "color": options["colors"][0]})
            assert call("POST", "/agents", body)[0] == 201, shape

    def test_every_offered_tier_is_accepted(self, api_table):
        _, options = call("GET", "/agents/options")

        for tier in [t["key"] for t in options["modelTiers"]]:
            body = dict(NEW_AGENT, name=f"Agent {tier}", modelTier=tier)
            assert call("POST", "/agents", body)[0] == 201, tier

    def test_every_offered_working_style_is_accepted(self, api_table):
        _, options = call("GET", "/agents/options")

        for style in options["workingStyles"]:
            body = dict(NEW_AGENT, name=f"Agent {style}", workingStyle=style)
            assert call("POST", "/agents", body)[0] == 201, style

    def test_a_fresh_organization_offers_no_connectors(self, api_table):
        """Nothing installed means nothing grantable, which is why the form
        shows an explanation rather than an empty list."""
        _, options = call("GET", "/agents/options")
        assert options["connectors"] == []

    def test_options_is_not_mistaken_for_an_agent_id(self, api_table):
        """`/agents/options` has to be matched before `/agents/{id}`, or it
        resolves to a 404 for an agent named "options"."""
        assert call("GET", "/agents/options")[0] == 200


class TestUpdateAndArchive:
    def test_archiving_hides_the_agent_without_deleting_it(self, api_table):
        call("POST", "/agents", NEW_AGENT)

        status, archived = call("DELETE", "/agents/cloud-operations")
        assert status == 200
        assert archived["status"] == "archived"

        assert call("GET", "/agents")[1]["agents"] == []
        # Still there, and still readable: evidence has to point somewhere.
        assert call("GET", "/agents/cloud-operations")[0] == 200
        assert len(call("GET", "/agents", qs={"status": "archived"})[1]["agents"]) == 1

    def test_an_unknown_field_is_refused_rather_than_ignored(self, api_table):
        """Silently dropping it would let a caller believe they changed
        something they did not."""
        call("POST", "/agents", NEW_AGENT)
        status, err = call("PATCH", "/agents/cloud-operations",
                           {"orgId": "someone-else"})
        assert status == 400
        assert "not editable" in err["detail"]

    def test_a_budget_change_is_recorded_against_the_person(self, api_table):
        call("POST", "/agents", NEW_AGENT)
        status, _ = call("PATCH", "/agents/cloud-operations",
                         {"budget": {"perRunUsd": 2.0, "perMonthUsd": 40.0}})
        assert status == 200

        _, agent = call("GET", "/agents/cloud-operations")
        actions = [e["action"] for e in agent["audit"]]
        assert "agent.budget_changed" in actions
        change = next(e for e in agent["audit"] if e["action"] == "agent.budget_changed")
        assert change["actorUserId"] == "owner-a"
        assert change["before"]["perMonthUsd"] == 30.0
        assert change["after"]["perMonthUsd"] == 40.0


class TestTenantIsolation:
    def test_another_tenant_cannot_read_or_archive_the_agent(self, api_table):
        call("POST", "/agents", NEW_AGENT)

        assert call("GET", "/agents/cloud-operations", sub="owner-b")[0] == 404
        assert call("GET", "/agents", sub="owner-b")[1]["agents"] == []
        assert call("DELETE", "/agents/cloud-operations", sub="owner-b")[0] == 404

        assert call("GET", "/agents/cloud-operations")[1]["status"] == "active"


class TestConsoleReadModel:
    """Additive routes the console needs to render real state instead of
    fixtures: an agent's skill assignments on its own profile, a room's
    owner/status at creation, and one global pending-approvals index for the
    Home inbox."""

    def test_agent_detail_includes_its_skill_assignments(self, api_table):
        from amazai import skills
        from amazai.store import Store

        call("POST", "/agents", NEW_AGENT)
        store = Store("owner-a", table=api_table)
        skills.create(
            store, {"name": "Amplify verify", "description": "Verify a rollout",
                    "owner": "cloud-operations"},
            created_by="owner-a")
        skills.assign(store, skill_id="amplify-verify", agent_id="cloud-operations",
                      version=1, assigned_by="owner-a")

        _, agent = call("GET", "/agents/cloud-operations")
        assert [a["skillId"] for a in agent["skillAssignments"]] == ["amplify-verify"]

    def test_a_room_thread_records_its_owner_and_starts_active(self, api_table):
        status, room = call("POST", "/threads",
                            {"kind": "room", "title": "Launch room",
                             "agentIds": ["cloud-operations"]})
        assert status == 201
        assert room["createdBy"] == "owner-a"
        assert room["status"] == "active"
        assert room["kind"] == "room"

    def test_global_approvals_route_lists_pending_across_runs(self, api_table):
        from amazai import approvals
        from amazai.policy import Capability
        from amazai.store import Store

        store = Store("owner-a", table=api_table)
        run = {"pk": K.run_pk("run-1"), "runId": "run-1", "threadId": "th-1",
              "agentId": "cloud-operations"}
        approvals.request(store, run, action="aws.restart_service",
                          arguments={"service": "web"}, why="rollout stuck",
                          capability=Capability.DESTRUCTIVE)

        status, body = call("GET", "/approvals")
        assert status == 200
        assert len(body["approvals"]) == 1
        assert body["approvals"][0]["action"] == "aws.restart_service"
        assert body["approvals"][0]["status"] == "pending"

    def test_global_approvals_route_is_tenant_scoped(self, api_table):
        from amazai import approvals
        from amazai.policy import Capability
        from amazai.store import Store

        store = Store("owner-a", table=api_table)
        run = {"pk": K.run_pk("run-1"), "runId": "run-1", "threadId": "th-1",
              "agentId": "cloud-operations"}
        approvals.request(store, run, action="aws.restart_service",
                          arguments={}, why="x", capability=Capability.DESTRUCTIVE)

        assert call("GET", "/approvals", sub="owner-b")[1]["approvals"] == []

    def test_agent_to_agent_messages_are_excluded_from_plain_thread_messages(self, api_table):
        """collab.send writes to the same thread pk a room lives on, but a
        room's plaintext chat must never quietly include agent<->agent
        traffic as if it were a bubble in the conversation with the owner."""
        from amazai import collab
        from amazai.store import Store

        store = Store("owner-a", table=api_table)
        store.put({"pk": K.agent_pk(store.owner_id, "eng"), "sk": "META", "entity": "Agent", "agentId": "eng"})
        store.put({"pk": K.agent_pk(store.owner_id, "ops"), "sk": "META", "entity": "Agent", "agentId": "ops"})
        call("POST", "/threads", {"kind": "room", "title": "Launch room",
                                  "agentIds": ["eng", "ops"]})
        threads = call("GET", "/threads")[1]["threads"]
        room_id = next(t["threadId"] for t in threads if t["title"] == "Launch room")

        collab.send(store, sender_agent_id="eng", recipient_agent_id="ops",
                   args={"text": "handing you the alarm", "collaboration_context_id": room_id})

        thread = call("GET", f"/threads/{room_id}")[1]
        assert thread["messages"] == []

        coord = call("GET", f"/threads/{room_id}/coordination")[1]["coordination"]
        assert len(coord) == 1
        assert coord[0]["kind"] == "message"
        assert coord[0]["fromAgentId"] == "eng"
        assert coord[0]["toAgentId"] == "ops"
        assert coord[0]["priority"] == "Deferred — delivered on next turn"

    def test_coordination_feed_includes_handoffs_for_the_threads_runs(self, api_table):
        from amazai import keys as K2, runs
        from amazai.store import Store

        store = Store("owner-a", table=api_table)
        call("POST", "/threads", {"kind": "room", "title": "Ops room", "agentIds": ["eng"]})
        room_id = next(t["threadId"] for t in call("GET", "/threads")[1]["threads"]
                      if t["title"] == "Ops room")
        run = runs.create(store, agent_id="eng", thread_id=room_id, goal="investigate 5xx")
        store.put({"pk": run["pk"], "sk": K2.handoff_sk("hoff-1"), "entity": "Handoff",
                  "handoffId": "hoff-1", "fromAgentId": "eng", "toAgentId": "ops",
                  "goal": "confirm the fix held", "status": "proposed"})

        coord = call("GET", f"/threads/{room_id}/coordination")[1]["coordination"]
        assert [c["kind"] for c in coord] == ["handoff"]
        assert coord[0]["status"] == "proposed"
        assert coord[0]["taskId"] == run["runId"]


class TestFirstBot:
    """Through the real handler: the parts of the first-Bot rule that only the
    listing and the second request can show."""

    FIRST = {"name": "Chief", "entrypoint": True, "operatorName": "Jaylen",
             "avatar": {"shape": "pebble", "color": "#2f6fe4"}}

    def test_the_first_bot_opens_its_own_conversation(self, api_table):
        status, agent = call("POST", "/agents", self.FIRST)
        assert status == 201
        assert agent["entrypoint"] is True and agent["title"] == "Chief"

        status, thread = call("GET", "/threads/dm-chief")
        assert status == 200
        first = thread["messages"][0]
        assert first["role"] == "assistant"
        assert first["text"].startswith("Hey Jaylen — good to meet you.")
        assert first["suggestions"]

    def test_a_second_first_bot_is_a_conflict_and_leaves_nothing_behind(self, api_table):
        call("POST", "/agents", self.FIRST)
        status, body = call("POST", "/agents", {**self.FIRST, "name": "Second"})
        assert status == 409 and body["error"] == "conflict"

        _, listing = call("GET", "/agents")
        assert [a["agentId"] for a in listing["agents"]] == ["chief"]

    def test_the_same_key_returns_the_same_first_bot_not_a_conflict(self, api_table):
        """A retried setup must resolve to the Bot it already made."""
        headers = {"idempotency-key": "setup-1"}
        call("POST", "/agents", self.FIRST, headers=headers)
        status, again = call("POST", "/agents", self.FIRST, headers=headers)
        assert status == 200 and again["agentId"] == "chief"

    def test_an_archived_first_bot_frees_the_place_for_another(self, api_table):
        call("POST", "/agents", self.FIRST)
        call("DELETE", "/agents/chief")
        status, agent = call("POST", "/agents", {**self.FIRST, "name": "Second"})
        assert status == 201 and agent["entrypoint"] is True

    def test_entrypoint_cannot_be_patched_onto_an_existing_agent(self, api_table):
        call("POST", "/agents", NEW_AGENT)
        status, body = call("PATCH", "/agents/cloud-operations", {"entrypoint": True})
        assert status == 400 and "not editable" in body["detail"]


class _FakeLambda:
    def __init__(self):
        self.invocations: list[dict] = []

    def invoke(self, **kwargs):
        self.invocations.append(kwargs)


class TestSignupWarmsTheAccountHarness:
    """A brand-new Auth0 subject's first authenticated request should start
    the shared harness Chief will need (see `api._warm_account_harness`),
    without making that request wait on it -- only through the real handler,
    since the trigger lives in its dispatch, not in any one route."""

    CONTEXT = SimpleNamespace(
        invoked_function_arn="arn:aws:lambda:us-west-2:1:function:amazai-api")

    def test_a_brand_new_subject_fires_an_async_self_invoke(self, api_table, monkeypatch):
        fake = _FakeLambda()
        monkeypatch.setattr(api.boto3, "client", lambda *_a, **_k: fake)

        resp = api.handler(event("GET", "/agents"), self.CONTEXT)

        assert resp["statusCode"] == 200
        assert len(fake.invocations) == 1
        call_kwargs = fake.invocations[0]
        assert call_kwargs["FunctionName"] == self.CONTEXT.invoked_function_arn
        assert call_kwargs["InvocationType"] == "Event"
        assert json.loads(call_kwargs["Payload"]) == {"provisionOwner": "owner-a"}

    def test_a_returning_subject_does_not_refire_it(self, api_table, monkeypatch):
        fake = _FakeLambda()
        monkeypatch.setattr(api.boto3, "client", lambda *_a, **_k: fake)

        api.handler(event("GET", "/agents"), self.CONTEXT)
        api.handler(event("GET", "/agents"), self.CONTEXT)

        assert len(fake.invocations) == 1

    def test_without_a_lambda_context_it_is_a_quiet_no_op(self, api_table, monkeypatch):
        def refuse(*_a, **_k):
            raise AssertionError("must not touch Lambda without a real context")
        monkeypatch.setattr(api.boto3, "client", refuse)

        resp = api.handler(event("GET", "/agents"), None)

        assert resp["statusCode"] == 200

    def test_a_lambda_invoke_failure_never_fails_the_signup_itself(self, api_table, monkeypatch):
        def boom(*_a, **_k):
            raise RuntimeError("throttled")
        monkeypatch.setattr(api.boto3, "client", boom)

        resp = api.handler(event("GET", "/agents"), self.CONTEXT)

        assert resp["statusCode"] == 200

    def test_the_async_branch_provisions_the_owners_shared_harness(self, monkeypatch, table):
        monkeypatch.setattr(api, "Store", lambda owner_id: Store(owner_id, table=table))
        monkeypatch.setattr(
            api.standard_runtime, "ensure_shared_harness",
            lambda store, **kw: "arn:aws:bedrock-agentcore:us-west-2:1:harness/warm")

        result = api.handler({"provisionOwner": "owner-a"}, None)

        assert result == {"ok": True,
                          "harnessArn": "arn:aws:bedrock-agentcore:us-west-2:1:harness/warm"}

    def test_a_harness_that_is_not_ready_yet_is_reported_not_raised(self, monkeypatch, table):
        monkeypatch.setattr(api, "Store", lambda owner_id: Store(owner_id, table=table))

        def not_ready(store, **kw):
            raise RuntimeError("the account runtime is still being provisioned")
        monkeypatch.setattr(api.standard_runtime, "ensure_shared_harness", not_ready)

        result = api.handler({"provisionOwner": "owner-a"}, None)

        assert result["ok"] is False
        assert "RuntimeError" in result["error"]


class TestInboxRows:
    """What a row in the inbox knows about its conversation, through the real
    handler. The unread case is the one that was silently broken: a Bot's reply
    wrote a message and bumped nothing."""

    def _reply(self, monkeypatch, thread_id, agent_id, text, at):
        import handlers.orchestrator as orchestrator
        from amazai.cost import RunCost
        # Second-resolution timestamps: without pinning the clock, a reply in
        # the same second as the read marker would (correctly) count as read
        # and make this test pass or fail by the time of day.
        monkeypatch.setattr(orchestrator.threads, "now_iso", lambda: at)
        store = Store("owner-a", table=self.table)
        orchestrator._persist_message(
            store, {"threadId": thread_id, "runId": "run-1"},
            {"agentId": agent_id, "name": "Cloud Operations"}, text, RunCost())

    @pytest.fixture(autouse=True)
    def _table(self, api_table):
        self.table = api_table

    def test_a_new_bot_reads_as_unread_and_previews_its_greeting(self):
        call("POST", "/agents", {**NEW_AGENT, "operatorName": "Jaylen"})
        _, listing = call("GET", "/threads")
        row = next(t for t in listing["threads"] if t["threadId"] == "dm-cloud-operations")
        assert row["preview"].startswith("Hey Jaylen")
        assert row["previewRole"] == "assistant"
        assert row["unread"] is True

    def test_a_reply_after_the_thread_was_opened_makes_it_unread_again(self, monkeypatch):
        call("POST", "/agents", NEW_AGENT)
        call("POST", "/threads/dm-cloud-operations/read", {})
        _, listing = call("GET", "/threads")
        assert next(t for t in listing["threads"]
                    if t["threadId"] == "dm-cloud-operations")["unread"] is False

        self._reply(monkeypatch, "dm-cloud-operations", "cloud-operations",
                    "Both 5xx spikes came from\n\n  the same deploy.", "2099-01-01T00:00:00Z")

        _, listing = call("GET", "/threads")
        row = next(t for t in listing["threads"] if t["threadId"] == "dm-cloud-operations")
        assert row["unread"] is True
        # One line, whitespace collapsed: a preview must not draw as blank rows.
        assert row["preview"] == "Both 5xx spikes came from the same deploy."
        assert row["previewRole"] == "assistant"

    def test_a_long_reply_is_cut_to_a_preview_not_stored_whole(self, monkeypatch):
        from amazai import threads
        call("POST", "/agents", NEW_AGENT)
        self._reply(monkeypatch, "dm-cloud-operations", "cloud-operations",
                    "word " * 200, "2099-01-01T00:00:00Z")
        _, listing = call("GET", "/threads")
        row = next(t for t in listing["threads"] if t["threadId"] == "dm-cloud-operations")
        assert len(row["preview"]) <= threads.PREVIEW_MAX
        assert row["preview"].endswith("…")


class TestPinned:
    def test_pins_round_trip_in_the_order_chosen(self, api_table):
        status, body = call("PUT", "/settings", {"pinned": ["dm-eng", "room-ship", "dm-cos"]})
        assert status == 200 and body["pinned"] == ["dm-eng", "room-ship", "dm-cos"]
        _, again = call("GET", "/settings")
        assert again["pinned"] == ["dm-eng", "room-ship", "dm-cos"]

    def test_a_duplicate_pin_is_kept_once(self, api_table):
        _, body = call("PUT", "/settings", {"pinned": ["dm-eng", "dm-eng", "dm-ops"]})
        assert body["pinned"] == ["dm-eng", "dm-ops"]

    def test_pinning_does_not_reset_settings_it_did_not_send(self, api_table):
        call("PUT", "/settings", {"theme": "dark"})
        _, body = call("PUT", "/settings", {"pinned": ["dm-eng"]})
        assert body["theme"] == "dark"

    def test_an_account_that_never_pinned_reads_an_empty_list(self, api_table):
        _, body = call("GET", "/settings")
        assert body["pinned"] == []

    @pytest.mark.parametrize("bad", ["dm-eng", [1], ["has space"], ["../x"], [f"t{i}" for i in range(13)]])
    def test_a_malformed_or_oversized_pin_list_is_refused(self, api_table, bad):
        status, _ = call("PUT", "/settings", {"pinned": bad})
        assert status == 400


class TestRoomSize:
    ROOM = {"kind": "room", "title": "Ship it"}

    def test_a_full_room_is_allowed(self, api_table):
        cap = collab.MAX_ROOM_MEMBERS
        status, room = call("POST", "/threads", {**self.ROOM, "agentIds": [f"a{i}" for i in range(cap)]})
        assert status == 201 and len(room["agentIds"]) == cap

    def test_one_agent_past_the_cap_is_refused(self, api_table):
        cap = collab.MAX_ROOM_MEMBERS
        status, body = call("POST", "/threads", {**self.ROOM, "agentIds": [f"a{i}" for i in range(cap + 1)]})
        assert status == 400 and f"at most {cap}" in body["detail"]

    def test_the_cap_stays_inside_what_the_shared_harness_has_been_seen_to_survive(self):
        """Five members woken at once put five invocations on the one shared
        harness and none completed (orchestrator.WAKE_STAGGER_SECONDS, commit
        4b645de). Nothing has measured a room since; raising this needs that
        probe first."""
        assert collab.MAX_ROOM_MEMBERS <= 4

    def test_the_same_agent_twice_counts_once(self, api_table):
        status, _ = call("POST", "/threads", {**self.ROOM, "agentIds": ["a0"] * 9})
        assert status == 201

    def test_agent_ids_must_be_a_list_of_strings(self, api_table):
        status, _ = call("POST", "/threads", {**self.ROOM, "agentIds": "eng"})
        assert status == 400
