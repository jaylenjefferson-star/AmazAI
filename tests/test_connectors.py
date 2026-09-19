"""Pipedream behind the grants model.

The claim these tests defend is narrow and specific: adding Pipedream did not
add a second way to decide what an agent may do. Every tool still has to
survive catalog -> org install -> agent grant -> router.resolve_tools, and a
tool that does not survive all four is absent from the schema the model sees.
"""

import pytest

from amazai import connectors as C, keys as K, pipedream, policy, router
from amazai.policy import Capability
from tests.test_agents_api import NEW_AGENT, api_table, call  # noqa: F401

SLACK = "pipedream:slack"


class FakeProxy:
    """Stands in for Pipedream. Records what it was asked to do."""

    def __init__(self, result=None, fail=None):
        self.calls = []
        self._result = result or {"ok": True}
        self._fail = fail

    def proxy(self, **kwargs):
        self.calls.append(kwargs)
        if self._fail:
            raise self._fail
        if kwargs.get("allowed_prefixes") and not kwargs["target_url"].startswith(
                kwargs["allowed_prefixes"]):
            raise pipedream.TargetNotAllowed(kwargs["target_url"])
        return self._result


def install_slack(store, tools=None):
    return C.install(store, SLACK, account_id="apn_test123",
                     external_user_id="user-1", actor_user_id="user-1",
                     allowed_tools=tools)


def grant(store, agent_id, tools, capability="write"):
    store.put({
        "pk": K.agent_pk(agent_id), "sk": K.grant_sk(SLACK),
        "entity": "Grant", "agentId": agent_id, "connectorId": SLACK,
        "allowedTools": tools, "capability": capability,
    })


def an_agent(store, agent_id="eng"):
    return store.put({
        "pk": K.agent_pk(agent_id), "sk": "META", "entity": "Agent",
        "agentId": agent_id, "gsi1pk": "AGENTS", "gsi1sk": agent_id,
        "name": agent_id, "status": "active",
        "allowedTools": ["shell", "file_operations"],
    })


# --- the catalog is an allowlist -------------------------------------------

class TestCatalog:
    def test_an_app_outside_the_catalog_cannot_be_installed(self, store):
        """Pipedream offers thousands of apps. The breadth of their catalog is
        never the breadth of what an agent can reach."""
        with pytest.raises(C.UnknownConnector):
            C.install(store, "pipedream:stripe", account_id="apn_x",
                      external_user_id="u", actor_user_id="u")

    def test_the_endpoint_is_pinned_by_the_catalog_not_the_arguments(self):
        """The model controls a tool's arguments. If it also controlled the
        URL, a grant for chat.postMessage would be a grant for every Slack
        endpoint."""
        action = C.spec(SLACK).action("slack.post")
        assert action.target == "https://slack.com/api/chat.postMessage"
        assert action.arguments == ("channel", "text")

    def test_the_proof_of_concept_spans_the_approval_boundary(self):
        """One connector, one action either side of the line — which is what
        makes it a proof of the whole path rather than half of it."""
        read = policy.evaluate("slack.read", Capability.READ)
        post = policy.evaluate("slack.post", Capability.WRITE)
        assert read.required is False
        assert post.required is True
        assert "always-approve floor" in post.reason

    def test_a_sensitive_write_cannot_be_pre_approved_away(self, store):
        """Even with the connector installed, the grant held and the agent
        pre-approving it to itself."""
        decision = policy.evaluate("slack.post", Capability.WRITE,
                                   preapproved={"slack.post"})
        assert decision.required is True


# --- install, grant, resolve ------------------------------------------------

class TestResolution:
    def test_a_granted_tool_reaches_the_schema(self, store):
        an_agent(store)
        install_slack(store)
        grant(store, "eng", ["slack.read"])

        grants = C.router_grants(store, "eng")
        resolution = router.resolve_tools(router.ResolutionInput(
            agent_allowed_tools=frozenset({"shell"}), grants=grants))
        assert "slack.read" in resolution.tools

    def test_an_ungranted_tool_is_absent_not_refused(self, store):
        """Absent from the schema, so it cannot be argued for, injected into,
        or retried into existence."""
        an_agent(store)
        install_slack(store)
        grant(store, "eng", ["slack.read"])

        grants = C.router_grants(store, "eng")
        resolution = router.resolve_tools(router.ResolutionInput(
            agent_allowed_tools=frozenset({"shell"}), grants=grants))
        assert "slack.post" not in resolution.tools
        assert router.may_call("slack.post", resolution) is False

    def test_a_grant_without_an_org_install_contributes_nothing(self, store):
        """The agent row says it may; the organization never installed it.
        The intersection is empty, and the intersection is what counts."""
        an_agent(store)
        grant(store, "eng", ["slack.read", "slack.post"])

        assert C.router_grants(store, "eng") == []

    def test_a_grant_beyond_the_org_install_is_trimmed_at_resolution(self, store):
        """Belt and braces: validate_grants refuses to write this, and
        resolution would drop it anyway."""
        an_agent(store)
        install_slack(store, tools=["slack.read"])
        grant(store, "eng", ["slack.read", "slack.post"])

        grants = C.router_grants(store, "eng")
        assert grants[0].allowed_tools == frozenset({"slack.read"})

    def test_a_grant_for_a_tool_no_longer_in_the_catalog_is_dropped(self, store):
        an_agent(store)
        install_slack(store)
        store.put({
            "pk": K.agent_pk("eng"), "sk": K.grant_sk(SLACK),
            "entity": "Grant", "agentId": "eng", "connectorId": SLACK,
            "allowedTools": ["slack.read", "slack.retired_action"],
            "capability": "write",
        })
        assert C.router_grants(store, "eng")[0].allowed_tools == frozenset({"slack.read"})


# --- revocation -------------------------------------------------------------

class TestRevocation:
    def test_revoking_the_connector_empties_the_next_schema(self, store):
        an_agent(store)
        install_slack(store)
        grant(store, "eng", ["slack.read"])
        assert C.router_grants(store, "eng") != []

        C.revoke(store, SLACK)

        grants = C.router_grants(store, "eng")
        assert grants == []
        resolution = router.resolve_tools(router.ResolutionInput(
            agent_allowed_tools=frozenset({"shell"}), grants=grants))
        assert resolution.tools == ("shell",)

    def test_revoking_removes_the_grant_rows_from_every_agent(self, store):
        an_agent(store, "eng")
        an_agent(store, "ops")
        install_slack(store)
        grant(store, "eng", ["slack.read"])
        grant(store, "ops", ["slack.read"])

        result = C.revoke(store, SLACK)

        assert sorted(result["revokedFrom"]) == ["eng", "ops"]
        assert store.query(K.agent_pk("eng"), sk_prefix="GRANT#") == []
        assert store.query(K.agent_pk("ops"), sk_prefix="GRANT#") == []

    def test_revoking_one_agents_grant_leaves_the_others(self, store):
        an_agent(store, "eng")
        an_agent(store, "ops")
        install_slack(store)
        grant(store, "eng", ["slack.read"])
        grant(store, "ops", ["slack.read"])

        store.delete(K.agent_pk("eng"), K.grant_sk(SLACK))

        assert C.router_grants(store, "eng") == []
        assert C.router_grants(store, "ops") != []


# --- invocation -------------------------------------------------------------

class TestInvocation:
    def test_a_granted_call_reaches_the_proxy_with_the_account_reference(self, store):
        an_agent(store)
        install_slack(store)
        grant(store, "eng", ["slack.read"])
        client = FakeProxy({"messages": []})

        C.invoke(store, client, agent_id="eng", tool="slack.read",
                 arguments={"channel": "C1", "limit": 5},
                 grants=C.router_grants(store, "eng"))

        call = client.calls[0]
        assert call["account_id"] == "apn_test123"
        assert call["target_url"] == "https://slack.com/api/conversations.history"
        assert call["allowed_prefixes"] == ("https://slack.com/api/",)

    def test_an_ungranted_call_never_reaches_the_proxy(self, store):
        an_agent(store)
        install_slack(store)
        grant(store, "eng", ["slack.read"])
        client = FakeProxy()

        with pytest.raises(C.NotGranted):
            C.invoke(store, client, agent_id="eng", tool="slack.post",
                     arguments={"channel": "C1", "text": "hi"},
                     grants=C.router_grants(store, "eng"))
        assert client.calls == []

    def test_arguments_outside_the_catalog_are_dropped(self, store):
        """A model that adds a field is adding it to a request it does not
        get to shape."""
        an_agent(store)
        install_slack(store)
        grant(store, "eng", ["slack.post"])
        client = FakeProxy()

        C.invoke(store, client, agent_id="eng", tool="slack.post",
                 arguments={"channel": "C1", "text": "hi",
                            "as_user": True, "token": "xoxb-injected"},
                 grants=C.router_grants(store, "eng"))

        assert client.calls[0]["body"] == {"channel": "C1", "text": "hi"}

    def test_invocation_and_failure_are_both_logged(self, store):
        an_agent(store)
        install_slack(store)
        grant(store, "eng", ["slack.read"])

        C.invoke(store, FakeProxy(), agent_id="eng", tool="slack.read",
                 arguments={"channel": "C1"},
                 grants=C.router_grants(store, "eng"))

        with pytest.raises(pipedream.PipedreamError):
            C.invoke(store, FakeProxy(fail=pipedream.PipedreamError("upstream 500")),
                     agent_id="eng", tool="slack.read", arguments={"channel": "C1"},
                     grants=C.router_grants(store, "eng"))

        log = [e["action"] for e in store.query(K.connector_pk(SLACK), sk_prefix="LOG#")]
        assert "connector.invoked" in log
        assert "connector.invocation_failed" in log

    def test_a_target_outside_the_allowed_prefix_is_refused_before_the_call(self):
        client = pipedream.Pipedream(credentials={"client_id": "x",
                                                  "client_secret": "y"})
        with pytest.raises(pipedream.TargetNotAllowed):
            client.proxy(external_user_id="u", account_id="apn_1",
                         target_url="https://evil.example/api",
                         allowed_prefixes=("https://slack.com/api/",))


# --- credentials never come near this process -------------------------------

class TestCredentialIsolation:
    def test_listing_accounts_never_asks_for_credentials(self):
        """`include_credentials` would return the third party's access token —
        exactly the material this design exists to never hold."""
        seen = {}

        def fake_request(method, url, *, headers, body=None, timeout=20):
            seen["url"] = url
            if url.endswith("/oauth/token"):
                return 200, {"access_token": "pd-token", "expires_in": 3600}
            return 200, {"data": []}

        client = pipedream.Pipedream(
            credentials={"client_id": "x", "client_secret": "y"},
            request=fake_request)
        import os
        os.environ["PIPEDREAM_PROJECT_ID"] = "proj_test"
        client.accounts("user-1")

        assert "include_credentials" not in seen["url"]

    def test_no_connector_row_carries_a_secret(self, store):
        """The row is read back into the console, so it must hold references
        only — an account id, not a token."""
        row = install_slack(store)
        blob = str(row).lower()
        for forbidden in ("client_secret", "access_token", "refresh_token",
                          "credential", "xoxb", "bearer"):
            assert forbidden not in blob

    def test_a_connector_log_line_carries_no_arguments(self, store):
        """Logs name what happened, not what was said. Message bodies are the
        user's content and have no business in an audit row."""
        an_agent(store)
        install_slack(store)
        grant(store, "eng", ["slack.post"])

        C.invoke(store, FakeProxy(), agent_id="eng", tool="slack.post",
                 arguments={"channel": "C1", "text": "a private message"},
                 grants=C.router_grants(store, "eng"))

        log = store.query(K.connector_pk(SLACK), sk_prefix="LOG#")
        assert all("private message" not in str(e) for e in log)


# --- the routes -------------------------------------------------------------

class TestConnectorRoutes:
    """Install and revoke through the handler, and the effect on what
    Create-a-Bot may offer."""

    def test_installing_makes_the_connector_grantable(self, api_table):
        assert call("GET", "/agents/options")[1]["connectors"] == []

        status, row = call("POST", f"/connectors/{SLACK}/install",
                           {"accountId": "apn_test123"})
        assert status == 201
        assert row["allowedTools"] == ["slack.post", "slack.read"]

        _, options = call("GET", "/agents/options")
        assert [c["connectorId"] for c in options["connectors"]] == [SLACK]

    def test_an_agent_can_then_be_created_holding_a_subset(self, api_table):
        call("POST", f"/connectors/{SLACK}/install", {"accountId": "apn_test123"})

        status, agent = call("POST", "/agents", dict(
            NEW_AGENT, grants=[{"connectorId": SLACK, "allowedTools": ["slack.read"]}]))
        assert status == 201

        _, full = call("GET", f"/agents/{agent['agentId']}")
        assert full["grants"][0]["allowedTools"] == ["slack.read"]

    def test_a_grant_for_a_tool_outside_the_install_is_refused(self, api_table):
        call("POST", f"/connectors/{SLACK}/install",
             {"accountId": "apn_test123", "allowedTools": ["slack.read"]})

        status, err = call("POST", "/agents", dict(
            NEW_AGENT, grants=[{"connectorId": SLACK,
                                "allowedTools": ["slack.read", "slack.post"]}]))
        assert status == 403
        assert "slack.post" in err["detail"]

    def test_an_uncatalogued_app_cannot_be_installed(self, api_table):
        assert call("POST", "/connectors/pipedream:stripe/install",
                    {"accountId": "apn_x"})[0] == 404

    def test_installing_without_an_authorized_account_is_refused(self, api_table):
        """The account id is the proof that the owner authorized the app in
        Pipedream. Without it there is nothing to call as."""
        status, err = call("POST", f"/connectors/{SLACK}/install", {})
        assert status == 400
        assert "accountId" in err["detail"]

    def test_revoking_removes_it_from_the_options_and_from_agents(self, api_table):
        call("POST", f"/connectors/{SLACK}/install", {"accountId": "apn_test123"})
        _, agent = call("POST", "/agents", dict(
            NEW_AGENT, grants=[{"connectorId": SLACK, "allowedTools": ["slack.read"]}]))

        status, result = call("DELETE", f"/connectors/{SLACK}")
        assert status == 200
        assert result["revokedFrom"] == [agent["agentId"]]

        assert call("GET", "/agents/options")[1]["connectors"] == []
        assert call("GET", f"/agents/{agent['agentId']}")[1]["grants"] == []

    def test_install_and_revoke_are_both_logged(self, api_table):
        call("POST", f"/connectors/{SLACK}/install", {"accountId": "apn_test123"})
        _, installed = call("GET", f"/connectors/{SLACK}")
        assert [e["action"] for e in installed["log"]] == ["connector.installed"]

        call("DELETE", f"/connectors/{SLACK}")

        from amazai.store import Store
        store = Store("owner-a", table=api_table)
        actions = {e["action"] for e in
                   store.query(K.connector_pk(SLACK), sk_prefix="LOG#")}
        assert actions == {"connector.installed", "connector.revoked"}

    def test_another_tenant_cannot_see_or_revoke_the_install(self, api_table):
        call("POST", f"/connectors/{SLACK}/install", {"accountId": "apn_test123"})

        assert call("GET", f"/connectors/{SLACK}", sub="owner-b")[0] == 404
        assert call("DELETE", f"/connectors/{SLACK}", sub="owner-b")[0] == 404
        assert call("GET", "/connectors", sub="owner-b")[1]["connectors"] == []

    def test_the_catalog_is_readable_without_installing_anything(self, api_table):
        status, payload = call("GET", "/connectors/catalog")
        assert status == 200
        assert [c["connectorId"] for c in payload["catalog"]] == [SLACK]
        tools = {a["tool"] for a in payload["catalog"][0]["actions"]}
        assert tools == {"slack.read", "slack.post"}
