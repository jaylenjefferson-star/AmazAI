"""One connector, walked end to end.

Everything except the network hop to Pipedream is the real code path: the
real handler, the real store, the real catalog, the real policy engine, the
real router. Only `Pipedream.proxy` is stubbed, because the live leg needs an
OAuth client this repository deliberately does not contain.

The test is written as a narrative because the claim it defends is a sequence
rather than a property: four gates, each of which narrows, and a write that
stops for a human at the end.
"""

import json

import pytest

from amazai import connectors as C, keys as K, policy, router
from amazai.policy import Capability
from amazai.store import Store

from tests.test_agents_api import api_table, call  # noqa: F401

SLACK = "pipedream:slack"

ANALYST = {
    "name": "Comms Analyst",
    "role": "Reads channel history and drafts replies.",
    "modelTier": "balanced",
    "avatar": {"shape": "cloud", "color": "#12a594"},
    "budget": {"perRunUsd": 1.0, "perMonthUsd": 10.0},
}


class RecordingProxy:
    def __init__(self):
        self.calls = []

    def proxy(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True, "messages": [{"text": "ship it"}]}


class TestOneConnectorEndToEnd:
    def test_the_whole_path(self, api_table):
        store = Store("owner-a", table=api_table)

        # 1 — Nothing is installed, so nothing is grantable. An agent created
        #     now has no outside access at all.
        assert call("GET", "/agents/options")[1]["connectors"] == []
        assert call("GET", "/connectors")[1]["connectors"] == []

        # The catalog is still readable: you can see what *could* be installed
        # without holding any of it.
        catalog = call("GET", "/connectors/catalog")[1]["catalog"]
        assert [c["connectorId"] for c in catalog] == [SLACK]

        # 2 — The organization installs Slack. The account id is a Pipedream
        #     reference; no credential is written.
        status, install = call("POST", f"/connectors/{SLACK}/install",
                               {"accountId": "apn_live123"})
        assert status == 201
        assert "secret" not in json.dumps(install).lower()
        assert install["accountId"] == "apn_live123"

        # 3 — An agent is created holding a strict subset: read, not post.
        status, agent = call("POST", "/agents", dict(
            ANALYST, grants=[{"connectorId": SLACK, "allowedTools": ["slack.read"]}]))
        assert status == 201
        agent_id = agent["agentId"]

        # 4 — Resolution. slack.read is in the schema the model will see.
        #     slack.post is not — not refused, absent.
        grants = C.router_grants(store, agent_id)
        resolution = router.resolve_tools(router.ResolutionInput(
            agent_allowed_tools=frozenset(agent["allowedTools"]), grants=grants))

        assert "slack.read" in resolution.tools
        assert "slack.post" not in resolution.tools
        assert router.may_call("slack.post", resolution) is False

        # 5 — The read runs. It is READ capability, so no approval is needed,
        #     and it goes out through the proxy with an account reference
        #     rather than a token.
        assert policy.evaluate("slack.read", Capability.READ).required is False

        client = RecordingProxy()
        result = C.invoke(store, client, agent_id=agent_id, tool="slack.read",
                          arguments={"channel": "C123", "limit": 10},
                          grants=grants, run_id="run-1")

        assert result["messages"] == [{"text": "ship it"}]
        sent = client.calls[0]
        assert sent["account_id"] == "apn_live123"
        assert sent["target_url"] == "https://slack.com/api/conversations.history"
        assert sent["body"] == {"channel": "C123", "limit": 10}

        # 6 — The write is refused, because this agent was never granted it.
        with pytest.raises(C.NotGranted):
            C.invoke(store, client, agent_id=agent_id, tool="slack.post",
                     arguments={"channel": "C123", "text": "hello"},
                     grants=grants, run_id="run-1")
        assert len(client.calls) == 1

        # 7 — Even granted, the write stops for a person. slack.post is on the
        #     always-approve floor, which no grant and no pre-approval clears.
        call("PATCH", f"/agents/{agent_id}", {"preapproved": ["slack.post"]})
        decision = policy.evaluate("slack.post", Capability.WRITE,
                                   preapproved={"slack.post"})
        assert decision.required is True
        assert "always-approve floor" in decision.reason

        # 8 — Revoking the connector empties the next schema immediately.
        call("DELETE", f"/connectors/{SLACK}")

        after = C.router_grants(store, agent_id)
        assert after == []
        assert router.resolve_tools(router.ResolutionInput(
            agent_allowed_tools=frozenset(agent["allowedTools"]),
            grants=after)).connector_tools == ()

        # 9 — And the whole sequence is on the record.
        log = [e["action"] for e in
               store.query(K.connector_pk(SLACK), sk_prefix="LOG#")]
        assert log == ["connector.installed", "connector.invoked",
                       "connector.revoked"]

    def test_a_revoke_mid_run_stops_the_very_next_call(self, api_table):
        """Not just the next run. The orchestrator re-reads grants before each
        connector call, so a revoke lands between two tool calls."""
        store = Store("owner-a", table=api_table)
        call("POST", f"/connectors/{SLACK}/install", {"accountId": "apn_live123"})
        _, agent = call("POST", "/agents", dict(
            ANALYST, grants=[{"connectorId": SLACK, "allowedTools": ["slack.read"]}]))
        agent_id = agent["agentId"]

        client = RecordingProxy()
        C.invoke(store, client, agent_id=agent_id, tool="slack.read",
                 arguments={"channel": "C1"},
                 grants=C.router_grants(store, agent_id))
        assert len(client.calls) == 1

        call("DELETE", f"/connectors/{SLACK}")

        with pytest.raises(C.NotGranted):
            C.invoke(store, client, agent_id=agent_id, tool="slack.read",
                     arguments={"channel": "C1"},
                     grants=C.router_grants(store, agent_id))
        assert len(client.calls) == 1
