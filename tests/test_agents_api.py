"""The agent routes, driven through the real Lambda handler.

These exist because the interesting failure is not in any one function: it is
what the database looks like after the harness call fails, which only the
handler's two-phase path can produce.
"""

import json

import pytest

from amazai import keys as K
from amazai.store import Store

import handlers.api as api


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
    "avatar": {"shape": "hex", "color": "#2f6fe4"},
    "budget": {"perRunUsd": 1.5, "perMonthUsd": 30.0},
}


@pytest.fixture
def api_table(table, monkeypatch):
    """The handler builds its own Store, so point it at moto's table and give
    every created agent a resolved model — the harness itself is stubbed."""
    monkeypatch.setattr(api, "_provision_harness", lambda store, agent: store.update(
        K.agent_pk(agent["agentId"]), "META",
        {"harnessArn": "arn:aws:bedrock-agentcore:us-west-2:1:harness/x",
         "status": "active", "state": "active"}))
    # The handler builds `Store(_owner(event))` itself, so replace the name it
    # reaches for rather than the class — patching __init__ would recurse.
    monkeypatch.setattr(api, "Store", lambda owner_id: Store(owner_id, table=table))
    return table


class TestCreate:
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
        assert store.query(K.agent_pk("cloud-operations"), sk_prefix="GRANT#") == []
        assert store.try_get(K.thread_pk("dm-cloud-operations"), "META") is None

    def test_a_failed_attempt_still_leaves_an_audit_trail(self, api_table, monkeypatch):
        """Rollback removes the agent. That it was attempted and why it failed
        is the one thing worth keeping."""
        monkeypatch.setattr(api, "_provision_harness",
                            lambda s, a: (_ for _ in ()).throw(RuntimeError("no model")))
        call("POST", "/agents", NEW_AGENT)

        store = Store("owner-a", table=api_table)
        trail = store.query(K.agent_pk("cloud-operations"), sk_prefix="AUDIT#")
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
            NEW_AGENT, grants=[{"connectorId": "pipedream:stripe",
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
