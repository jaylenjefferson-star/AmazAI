"""One app, walked end to end.

Everything except the network hop to Composio is the real code path: the real
handler, the real store, the real client, the real policy engine, and the
orchestrator's own `_handle_tool` -- the gate a model actually meets. Only the
transport under the Composio client is scripted, because the live leg needs a
project key this repository deliberately does not contain.

Written as a narrative because the claim it defends is a sequence rather than a
property: connect, use, be stopped at the write, be revoked.
"""

import json

from amazai import approvals, connectors as C, keys as K, review
from amazai.policy import Capability
from amazai.store import Store

from tests.fake_composio import DELETE, READ, WRITE, FakeTransport
from tests.loop_world import POST, SLACK, World, world  # noqa: F401
from tests.test_agents_api import api_table, call  # noqa: F401


class TestOneAppEndToEnd:
    def test_the_whole_path(self, api_table, monkeypatch):
        store = Store("owner-a", table=api_table)

        # 1 -- Nothing is connected. A Bot made now has no outside access, and
        #      says so instead of pretending.
        w = World(api_table, monkeypatch, connected=set(), install=False)
        assert call("GET", "/connectors")[1]["connectors"] == []
        assert w.search("post a message")["tools"] == []

        # 2 -- The person signs in to Slack on Composio's hosted page. We get a
        #      link, never a credential.
        _, link = call("POST", "/connectors/connect-token", {"connectorId": "slack"})
        assert link["connectLinkUrl"].startswith("https://connect.composio.dev/")
        assert call("POST", f"/connectors/{SLACK}/install", {})[0] == 409   # not signed in yet

        # 3 -- They finish signing in. Composio now reports an ACTIVE account, and
        #      only then is Slack installed -- and granted to the Bot that exists.
        w.transport.connected.add("slack")
        status, install = call("POST", f"/connectors/{SLACK}/install", {})
        assert status == 201 and w.agent_id in install["grantedTo"]
        assert "secret" not in json.dumps(install).lower() and install["accountId"] == "ca_slack"

        # 4 -- Restrict this Bot to reading. The write is not merely refused; it
        #      is absent from what the Bot is shown.
        store.put(C.grant_row(w.agent_id, SLACK, actor_user_id="owner-a", capability=Capability.READ))
        shown = {t["tool"] for t in w.search("slack")["tools"]}
        assert shown == {READ}

        # 5 -- The read runs, as the person, against their account reference.
        assert w.use(READ, {"channel": "C123"})["pause"] is False
        assert w.executed[0]["body"]["user_id"] == "owner-a"
        assert w.executed[0]["body"]["connected_account_id"] == "ca_slack"
        assert w.last["review"]["rule"] == "read"

        # 6 -- The write is refused by the ceiling; nothing left the building.
        blocked = w.use(WRITE, POST, tool_use_id="tu-2")
        assert "read-only" in blocked["toolResult"]["error"]
        assert len(w.executed) == 1

        # 7 -- The owner widens the Bot to the whole app. The write now stops for a
        #      person, and only that exact call runs once approved.
        store.put(C.grant_row(w.agent_id, SLACK, actor_user_id="owner-a"))
        held = w.use(WRITE, POST, tool_use_id="tu-3")
        assert held["pause"] is True and len(w.executed) == 1
        w.approve(held["approval"])
        assert w.use(WRITE, POST, tool_use_id="tu-4")["pause"] is False
        assert w.last["review"]["rule"] == "approved" and len(w.executed) == 2
        assert w.use(WRITE, POST, tool_use_id="tu-5")["pause"] is True   # spent

        # 8 -- A destructive tool asks even if the owner pre-approved it.
        w.agent = {**w.agent, "preapproved": [DELETE]}
        assert w.use(DELETE, {"channel": "C1", "ts": "1"}, tool_use_id="tu-6")["pause"] is True

        # 9 -- Revoking removes it from the very next call.
        call("DELETE", f"/connectors/{SLACK}")
        assert C.granted_apps(store, w.agent_id) == []
        gone = w.use(READ, {"channel": "C123"}, tool_use_id="tu-7")
        assert "not connected" in gone["toolResult"]["error"]
        assert len(w.executed) == 2

        # 10 -- And the sequence is on the record, with no arguments in it.
        log = store.query(K.connector_pk(SLACK), sk_prefix="LOG#")
        actions = [e["action"] for e in log]
        assert actions[0] == "connector.authorization_started"
        assert actions.count("connector.invoked") == 2
        assert actions[-1] == "connector.revoked" and "connector.installed" in actions
        assert "Shipped." not in json.dumps(log)

    def test_a_failing_provider_call_is_a_clear_error_and_leaves_a_log_id(self, api_table, monkeypatch):
        w = World(api_table, monkeypatch)
        w.transport.fail[READ] = "not_in_channel"
        out = w.use(READ, {"channel": "C1"})
        assert "not_in_channel" in out["toolResult"]["error"]
        log = w.store.query(K.connector_pk(SLACK), sk_prefix="LOG#")
        failed = [e for e in log if e["action"] == "connector.invocation_failed"]
        assert failed and "log_failed" in failed[0]["detail"]
