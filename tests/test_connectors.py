"""Composio behind the grants model.

The claim these tests defend: opening the catalog to every app did not open the
gate. Any app can be connected; what a Bot may *do* in it is still decided per
call, from the tool's own tags, by code -- reads run, writes ask, an unlabelled
tool counts as a write, and a Bot's grant can only ever narrow that.
"""

import json

import pytest

from amazai import connectors as C, keys as K
from amazai.policy import Capability
from tests.fake_composio import FakeTransport, client, wire
from tests.test_agents_api import NEW_AGENT, api_table, call  # noqa: F401

SLACK = "composio:slack"
GMAIL = "composio:gmail"


def install(store, slug="slack"):
    return C.install(store, C.connector_id(slug), name=slug.title(), account_id=f"ca_{slug}",
                     external_user_id="owner-a", actor_user_id="owner-a")


def grant(store, agent_id, cid=SLACK, **kw):
    return store.put(C.grant_row(agent_id, cid, actor_user_id="owner-a", **kw))


def an_agent(store, agent_id="eng", status="active"):
    return store.put({
        "pk": K.agent_pk(agent_id), "sk": "META", "entity": "Agent",
        "agentId": agent_id, "gsi1pk": "AGENTS", "gsi1sk": agent_id,
        "name": agent_id, "status": status, "allowedTools": ["shell", "file_operations"],
    })


class TestAnyAppCanBeConnected:
    def test_an_app_nobody_wrote_a_catalog_entry_for_installs(self, store):
        row = install(store, "github")
        assert row["connectorId"] == "composio:github"
        assert row["allowedTools"] == [C.WILDCARD]
        assert "composio:github" in C.installed(store)

    def test_the_bare_name_and_the_namespaced_id_are_one_connector(self):
        assert C.slug_of("gmail") == C.slug_of("composio:gmail") == C.slug_of("Gmail") == "gmail"

    @pytest.mark.parametrize("bad", ["", "composio:", "../x", "a b", "composio:a/b", "x" * 80, None])
    def test_a_connector_id_that_is_not_a_safe_slug_is_refused(self, bad):
        with pytest.raises(C.UnknownConnector):
            C.slug_of(bad)

    def test_no_stored_row_carries_a_credential(self, store):
        an_agent(store)
        install(store)
        grant(store, "eng")
        C.invoke(store, client()[0], agent_id="eng",
                 grant=C.granted_apps(store, "eng")[0], tool="SLACK_FETCH_CONVERSATION_HISTORY",
                 arguments={"channel": "C1"})
        rows = [json.dumps(r, default=str).lower() for r in store.query_index(
            "gsi1", "gsi1pk", "CONNECTORS", limit=50)]
        rows += [json.dumps(r, default=str).lower() for r in store.query(K.connector_pk("owner-a", SLACK))]
        for text in rows:
            assert "ak_" not in text and "token" not in text.replace("accountid", "")

    def test_rows_a_previous_provider_left_are_not_connectors_any_more(self, store):
        store.put({"pk": K.connector_pk("owner-a", "pipedream:slack"), "sk": "META", "entity": "Connector",
                   "connectorId": "pipedream:slack", "gsi1pk": "CONNECTORS", "gsi1sk": "Slack",
                   "app": "slack", "status": "installed", "allowedTools": ["slack.read"]})
        assert C.installed(store) == {}


class TestTwoOwnersConnectingTheSameApp:
    """`connector_id` is derived from the app slug, so it is identical for
    every owner who connects Slack -- unlike a run/agent/thread id, which
    `new_id()` already makes globally unique. Without `owner_id` in the
    connector row's own pk, the second owner's `install()` would silently
    overwrite the first owner's row: same pk/sk, an unconditional `put_item`,
    no uniqueness check."""

    def test_installing_does_not_touch_the_other_owners_row(self, two_stores):
        store_a, store_b = two_stores
        row_a = install(store_a)
        row_b = C.install(store_b, C.connector_id("slack"), name="Slack B",
                          account_id="ca_slack_b", external_user_id="owner-b",
                          actor_user_id="owner-b")

        assert row_a["pk"] != row_b["pk"]
        fresh_a = store_a.get(K.connector_pk("owner-a", SLACK), "META")
        assert fresh_a["accountId"] == row_a["accountId"] == "ca_slack"
        assert fresh_a["externalUserId"] == "owner-a"

    def test_each_owner_sees_only_their_own_install(self, two_stores):
        store_a, store_b = two_stores
        install(store_a)
        C.install(store_b, C.connector_id("slack"), name="Slack B", account_id="ca_slack_b",
                 external_user_id="owner-b", actor_user_id="owner-b")

        assert SLACK in C.installed(store_a)
        assert C.installed(store_a)[SLACK]["accountId"] == "ca_slack"
        assert SLACK in C.installed(store_b)
        assert C.installed(store_b)[SLACK]["accountId"] == "ca_slack_b"

    def test_revoking_one_owners_connector_leaves_the_others_intact(self, two_stores):
        store_a, store_b = two_stores
        install(store_a)
        C.install(store_b, C.connector_id("slack"), name="Slack B", account_id="ca_slack_b",
                 external_user_id="owner-b", actor_user_id="owner-b")

        C.revoke(store_a, SLACK)

        assert C.installed(store_a) == {}
        assert SLACK in C.installed(store_b)

    def test_invocation_logs_do_not_cross_owners(self, two_stores):
        # Distinct agent ids for the two owners -- agentId is itself a
        # separate, larger pre-existing collision (K.agent_pk is not
        # owner-scoped either) that this test is not about; see the
        # connector-isolation tests above for the bug this class covers.
        store_a, store_b = two_stores
        an_agent(store_a, "eng-a")
        an_agent(store_b, "eng-b")
        install(store_a)
        C.install(store_b, C.connector_id("slack"), name="Slack B", account_id="ca_slack_b",
                 external_user_id="owner-b", actor_user_id="owner-b")
        grant(store_a, "eng-a")
        store_b.put(C.grant_row("eng-b", SLACK, actor_user_id="owner-b"))

        C.invoke(store_a, client()[0], agent_id="eng-a", grant=C.granted_apps(store_a, "eng-a")[0],
                 tool="SLACK_FETCH_CONVERSATION_HISTORY", arguments={"channel": "C1"})

        log_a = store_a.query(K.connector_pk("owner-a", SLACK), sk_prefix="LOG#")
        log_b = store_b.query(K.connector_pk("owner-b", SLACK), sk_prefix="LOG#")
        assert len(log_a) == 1
        assert log_b == []


class TestResolution:
    def test_a_granted_app_is_resolved_for_the_bot(self, store):
        an_agent(store)
        install(store)
        grant(store, "eng")
        [g] = C.granted_apps(store, "eng")
        assert (g.slug, g.capability, g.tools) == ("slack", Capability.ADMIN, frozenset({"*"}))

    def test_an_app_the_bot_was_not_given_is_absent_not_refused(self, store):
        an_agent(store)
        an_agent(store, "ops")
        install(store)
        grant(store, "eng")
        assert C.granted_apps(store, "ops") == []

    def test_a_grant_without_an_org_install_contributes_nothing(self, store):
        an_agent(store)
        grant(store, "eng")
        assert C.granted_apps(store, "eng") == []

    def test_a_grant_whose_ceiling_cannot_be_read_falls_to_the_lowest(self, store):
        an_agent(store)
        install(store)
        store.put({**C.grant_row("eng", SLACK, actor_user_id="x"), "capability": "banana"})
        assert C.granted_apps(store, "eng")[0].capability is Capability.READ

    def test_an_explicit_tool_list_allows_only_those_tools(self, store):
        an_agent(store)
        install(store)
        grant(store, "eng", tools=["SLACK_FETCH_CONVERSATION_HISTORY"])
        granted = C.granted_apps(store, "eng")
        C.authorize(granted, toolkit_slug="slack", tool="SLACK_FETCH_CONVERSATION_HISTORY",
                    capability=Capability.READ)
        with pytest.raises(C.NotGranted, match="not among the tools"):
            C.authorize(granted, toolkit_slug="slack", tool="SLACK_SEND_MESSAGE",
                        capability=Capability.WRITE)

    def test_a_read_only_grant_is_a_read_only_bot_whatever_the_tool_is_called(self, store):
        an_agent(store)
        install(store)
        grant(store, "eng", capability=Capability.READ)
        granted = C.granted_apps(store, "eng")
        C.authorize(granted, toolkit_slug="slack", tool="SLACK_ANYTHING", capability=Capability.READ)
        for cap in (Capability.WRITE, Capability.DESTRUCTIVE):
            with pytest.raises(C.NotGranted, match="read-only"):
                C.authorize(granted, toolkit_slug="slack", tool="SLACK_ANYTHING", capability=cap)

    def test_an_app_that_is_not_connected_for_the_bot_is_refused_by_name(self, store):
        an_agent(store)
        install(store)
        grant(store, "eng")
        with pytest.raises(C.NotGranted, match="gmail is not connected"):
            C.authorize(C.granted_apps(store, "eng"), toolkit_slug="gmail", tool="GMAIL_X",
                        capability=Capability.READ)


class TestConnectingMeansUsable:
    def test_every_active_bot_gets_the_app_and_archived_ones_do_not(self, store):
        an_agent(store, "eng")
        an_agent(store, "ops")
        an_agent(store, "old", status="archived")
        install(store)
        assert sorted(C.grant_to_active_agents(store, SLACK, actor_user_id="owner-a")) == ["eng", "ops"]
        assert C.granted_apps(store, "old") == []

    def test_a_grant_someone_narrowed_is_never_widened_by_reconnecting(self, store):
        an_agent(store)
        install(store)
        grant(store, "eng", capability=Capability.READ)
        assert C.grant_to_active_agents(store, SLACK, actor_user_id="owner-a") == []
        assert C.granted_apps(store, "eng")[0].capability is Capability.READ

    def test_a_new_bot_the_owner_creates_starts_with_every_connected_app(self, store):
        install(store)
        install(store, "gmail")
        assert {g["connectorId"] for g in C.default_grants(store)} == {SLACK, GMAIL}


class TestRevocation:
    def test_revoking_empties_the_next_resolution(self, store):
        an_agent(store)
        install(store)
        grant(store, "eng")
        assert C.granted_apps(store, "eng")
        C.revoke(store, SLACK)
        assert C.granted_apps(store, "eng") == []

    def test_revoking_removes_the_grant_from_every_bot_and_only_that_app(self, store):
        an_agent(store, "eng")
        an_agent(store, "ops")
        install(store)
        install(store, "gmail")
        for a in ("eng", "ops"):
            grant(store, a)
            grant(store, a, GMAIL)
        result = C.revoke(store, SLACK)
        assert sorted(result["revokedFrom"]) == ["eng", "ops"]
        assert [g.slug for g in C.granted_apps(store, "eng")] == ["gmail"]


class TestInvocation:
    def test_a_call_reaches_composio_as_the_owner_with_the_account_reference(self, store):
        an_agent(store)
        install(store)
        grant(store, "eng")
        c, t = client()
        C.invoke(store, c, agent_id="eng", grant=C.granted_apps(store, "eng")[0],
                 tool="SLACK_FETCH_CONVERSATION_HISTORY", arguments={"channel": "C1"})
        assert t.executed[0]["body"] == {"user_id": "owner-a", "arguments": {"channel": "C1"},
                                        "connected_account_id": "ca_slack"}

    def test_after_a_revoke_the_call_is_refused_before_it_leaves(self, store):
        an_agent(store)
        install(store)
        grant(store, "eng")
        held = C.granted_apps(store, "eng")[0]
        C.revoke(store, SLACK)
        c, t = client()
        with pytest.raises(C.NotInstalled):
            C.invoke(store, c, agent_id="eng", grant=held, tool="SLACK_FETCH_CONVERSATION_HISTORY",
                     arguments={})
        assert t.executed == []

    def test_invocation_and_failure_are_both_logged_with_the_log_id(self, store):
        an_agent(store)
        install(store)
        grant(store, "eng")
        held = C.granted_apps(store, "eng")[0]
        c, _ = client(FakeTransport(fail={"SLACK_SEND_MESSAGE": "nope"}))
        C.invoke(store, c, agent_id="eng", grant=held, tool="SLACK_FETCH_CONVERSATION_HISTORY",
                 arguments={"channel": "C1"}, run_id="run_1")
        with pytest.raises(Exception, match="nope"):
            C.invoke(store, c, agent_id="eng", grant=held, tool="SLACK_SEND_MESSAGE",
                     arguments={}, run_id="run_1")
        log = store.query(K.connector_pk("owner-a", SLACK), sk_prefix="LOG#")
        by_action = {e["action"]: e for e in log}
        assert "log_ok" in by_action["connector.invoked"]["detail"]
        assert by_action["connector.invocation_failed"]["outcome"] == "error"
        assert "log_failed" in by_action["connector.invocation_failed"]["detail"]

    def test_a_log_line_carries_no_arguments(self, store):
        an_agent(store)
        install(store)
        grant(store, "eng")
        C.invoke(store, client()[0], agent_id="eng", grant=C.granted_apps(store, "eng")[0],
                 tool="SLACK_SEND_MESSAGE", arguments={"text": "the launch is on the 14th"})
        assert "launch" not in json.dumps(store.query(K.connector_pk("owner-a", SLACK), sk_prefix="LOG#"))


class TestConnectorRoutes:
    """Through the handler: what the console actually calls."""

    def test_every_app_is_listed_and_searchable(self, api_table, monkeypatch):
        wire(monkeypatch)
        status, page = call("GET", "/connectors/apps")
        assert status == 200
        assert {a["slug"] for a in page["apps"]} == {"slack", "gmail", "github"}
        assert [a["slug"] for a in call("GET", "/connectors/apps", qs={"q": "mail"})[1]["apps"]] == ["gmail"]

    def test_the_old_hand_written_catalog_route_is_gone(self, api_table, monkeypatch):
        wire(monkeypatch)
        assert call("GET", "/connectors/catalog")[0] == 404

    def test_a_connect_link_is_issued_and_the_start_is_logged(self, api_table, monkeypatch):
        wire(monkeypatch)
        status, link = call("POST", "/connectors/connect-token", {"connectorId": "gmail"},
                            headers={"origin": "https://amazai.co"})
        assert status == 201 and link["connectLinkUrl"].startswith("https://connect.composio.dev/")
        log = call("GET", f"/connectors/{GMAIL}")
        # Nothing is installed by asking for a link.
        assert log[0] == 404

    def test_the_callback_goes_only_to_a_known_console_origin(self, api_table, monkeypatch):
        _, t = wire(monkeypatch)
        call("POST", "/connectors/connect-token", {"connectorId": "gmail"},
             headers={"origin": "https://evil.example"})
        link = next(r for r in t.requests if r["path"].endswith("/link"))
        assert "callback_url" not in link["body"]
        call("POST", "/connectors/connect-token", {"connectorId": "gmail"},
             headers={"origin": "https://amazai.co"})
        link = [r for r in t.requests if r["path"].endswith("/link")][-1]
        assert link["body"]["callback_url"] == "https://amazai.co/marketplace?connected=gmail"

    def test_installing_before_signing_in_is_refused_plainly(self, api_table, monkeypatch):
        wire(monkeypatch, FakeTransport(connected=set()))
        status, body = call("POST", f"/connectors/{GMAIL}/install", {})
        assert status == 409 and body["error"] == "not_connected"
        assert C.installed(__import__("amazai.store", fromlist=["Store"]).Store("owner-a", table=api_table)) == {}

    def test_installing_a_connected_app_grants_it_to_the_bots_that_exist(self, api_table, monkeypatch):
        wire(monkeypatch, FakeTransport(connected={"gmail"}))
        _, agent = call("POST", "/agents", NEW_AGENT)
        status, row = call("POST", f"/connectors/{GMAIL}/install", {})
        assert status == 201, row
        assert row["name"] == "Gmail" and agent["agentId"] in row["grantedTo"]
        assert call("GET", f"/agents/{agent['agentId']}")[1]["grants"][0]["connectorId"] == GMAIL

    def test_a_bot_created_afterwards_starts_with_the_connected_app(self, api_table, monkeypatch):
        wire(monkeypatch, FakeTransport(connected={"gmail"}))
        call("POST", f"/connectors/{GMAIL}/install", {})
        _, agent = call("POST", "/agents", NEW_AGENT)
        assert [g["connectorId"] for g in call("GET", f"/agents/{agent['agentId']}")[1]["grants"]] == [GMAIL]

    def test_a_request_that_names_no_grants_is_taken_as_written(self, api_table, monkeypatch):
        wire(monkeypatch, FakeTransport(connected={"gmail"}))
        call("POST", f"/connectors/{GMAIL}/install", {})
        _, agent = call("POST", "/agents", {**NEW_AGENT, "grants": []})
        assert call("GET", f"/agents/{agent['agentId']}")[1]["grants"] == []

    def test_a_bot_another_bot_proposes_never_inherits_connectors(self):
        import handlers.orchestrator as orch
        proposal = orch._agent_creation_proposal({"name": "X", "role": "y"}, parent_agent_id="eng")
        assert proposal["grants"] == []

    def test_an_id_the_browser_encodes_still_finds_the_connector(self, api_table, monkeypatch):
        wire(monkeypatch, FakeTransport(connected={"gmail"}))
        call("POST", "/connectors/composio%3Agmail/install", {})
        assert call("GET", "/connectors/composio%3Agmail")[0] == 200
        assert call("DELETE", "/connectors/composio%3Agmail")[0] == 200

    def test_revoking_removes_it_and_is_logged(self, api_table, monkeypatch):
        wire(monkeypatch, FakeTransport(connected={"gmail"}))
        call("POST", f"/connectors/{GMAIL}/install", {})
        assert call("DELETE", f"/connectors/{GMAIL}")[0] == 200
        assert call("GET", "/connectors")[1]["connectors"] == []

    def test_another_tenant_cannot_see_or_revoke_the_install(self, api_table, monkeypatch):
        wire(monkeypatch, FakeTransport(connected={"gmail"}))
        call("POST", f"/connectors/{GMAIL}/install", {})
        assert call("GET", f"/connectors/{GMAIL}", sub="owner-b")[0] == 404
        assert call("DELETE", f"/connectors/{GMAIL}", sub="owner-b")[0] == 404
        assert call("GET", "/connectors", sub="owner-b")[1]["connectors"] == []

    def test_when_composio_is_unreachable_the_answer_is_a_clean_502(self, api_table, monkeypatch):
        import handlers.api as api
        from amazai import composio as cp

        class Down:
            def toolkits(self, **kw):
                raise cp.ComposioError("could not reach Composio")
        monkeypatch.setattr(api, "_composio", lambda: Down())
        status, body = call("GET", "/connectors/apps")
        assert status == 502 and body["error"] == "connector_unavailable"


class TestPerBotAccess:
    """The profile's Tools section: narrow or remove one app for one Bot."""

    def _bot_with_slack(self, api_table, monkeypatch):
        wire(monkeypatch, FakeTransport(connected={"slack"}))
        _, agent = call("POST", "/agents", NEW_AGENT)
        call("POST", f"/connectors/{SLACK}/install", {})
        return agent["agentId"]

    def test_a_person_can_make_one_bot_read_only_in_one_app(self, api_table, monkeypatch):
        bot = self._bot_with_slack(api_table, monkeypatch)
        status, row = call("PUT", f"/agents/{bot}/grants/{SLACK}", {"capability": "read"})
        assert status == 200 and row["capability"] == "read"
        from amazai.store import Store
        [g] = C.granted_apps(Store("owner-a", table=api_table), bot)
        assert g.capability is Capability.READ

    def test_the_change_is_audited_with_before_and_after(self, api_table, monkeypatch):
        bot = self._bot_with_slack(api_table, monkeypatch)
        call("PUT", f"/agents/{bot}/grants/{SLACK}", {"capability": "read"})
        from amazai.store import Store
        audit = [r for r in Store("owner-a", table=api_table).query(K.agent_pk(bot))
                 if r.get("action") == "agent.grants_changed"]
        assert audit and audit[-1]["after"]["grant"]["capability"] == "read"

    def test_a_bot_cannot_be_given_more_than_the_organization_holds(self, api_table, monkeypatch):
        bot = self._bot_with_slack(api_table, monkeypatch)
        status, err = call("PUT", f"/agents/{bot}/grants/composio:gmail", {"capability": "read"})
        assert status == 403 and "not installed" in err["detail"]

    def test_removing_an_app_from_one_bot_leaves_the_org_install(self, api_table, monkeypatch):
        bot = self._bot_with_slack(api_table, monkeypatch)
        assert call("DELETE", f"/agents/{bot}/grants/{SLACK}")[0] == 200
        from amazai.store import Store
        store = Store("owner-a", table=api_table)
        assert C.granted_apps(store, bot) == [] and SLACK in C.installed(store)
        assert call("DELETE", f"/agents/{bot}/grants/{SLACK}")[0] == 404   # already gone

    def test_another_tenant_cannot_touch_it(self, api_table, monkeypatch):
        bot = self._bot_with_slack(api_table, monkeypatch)
        assert call("PUT", f"/agents/{bot}/grants/{SLACK}", {"capability": "read"}, sub="owner-b")[0] == 404
        assert call("DELETE", f"/agents/{bot}/grants/{SLACK}", sub="owner-b")[0] == 404
