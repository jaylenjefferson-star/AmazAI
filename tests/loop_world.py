"""One Bot, connected to Slack, in the middle of a turn -- through the real gate.

Every suite that needs "the agent loop" builds it here. The store, the policy
engine, the router, the approvals and the orchestrator's own `_handle_tool` are
the real ones; the network is `fake_composio.FakeTransport` under the real
Composio client. Nothing above the network hop is stubbed.
"""

import types

import pytest

import handlers.orchestrator as orch
from amazai import approvals, keys as K, router, runs
from amazai.cost import RunCost
from amazai.evidence import EvidenceWriter
from amazai.push import Push
from amazai.store import Store
from tests.fake_composio import (DELETE, READ, UNLABELLED, WRITE, FakeTransport, wire)
from tests.test_agents_api import api_table, call  # noqa: F401

SLACK = "composio:slack"
POST = {"channel": "C123", "text": "Shipped."}


class RecPush(Push):
    """A `Push` that remembers instead of sending."""

    def __init__(self):
        self.sent = []

    def send(self, payload):
        self.sent.append(payload)
        return 0

    @property
    def steps(self):
        return [e for e in self.sent if e["type"] == "tool"]


def tool_call(name, args, tool_use_id="tu-1"):
    return types.SimpleNamespace(tool_name=name, tool_input=args, tool_use_id=tool_use_id)


class World:
    def __init__(self, table, monkeypatch, *, connected=None, install=True, grants=None):
        self.store = Store("owner-a", table=table)
        self.client, self.transport = wire(monkeypatch, FakeTransport(
            connected={"slack"} if connected is None else connected))
        if install:
            status, row = call("POST", f"/connectors/{SLACK}/install", {})
            assert status == 201, row
        body = {"name": "Comms", "role": "Reads and posts in Slack.", "modelTier": "balanced",
                "avatar": {"shape": "cloud", "color": "#12a594"},
                "budget": {"perRunUsd": 1.0, "perMonthUsd": 10.0}}
        if grants is not None:
            body["grants"] = grants
        status, agent = call("POST", "/agents", body)
        assert status == 201, agent
        self.agent_id = agent["agentId"]
        self.agent = self.store.get(K.agent_pk(self.agent_id), "META")
        self.run = runs.create(self.store, agent_id=self.agent_id,
                               thread_id=f"dm-{self.agent_id}", goal="post the update")
        self.push, self.ev, self.cost, self.turn = RecPush(), EvidenceWriter("run_test"), RunCost(), orch.Turn()
        self.resolution = router.resolve_tools(router.ResolutionInput(
            agent_allowed_tools=frozenset(self.agent["allowedTools"]), grants=[]))
        self.seq = 0

    def handle(self, name, args, tool_use_id="tu-1"):
        self.seq += 1
        return orch._handle_tool(self.store, self.run, self.agent, self.ev, self.push,
                                 self.resolution, tool_call(name, args, tool_use_id),
                                 self.seq, self.cost, self.turn)

    def use(self, tool, arguments, tool_use_id="tu-1"):
        """The model calls a connector tool, the only way it can."""
        return self.handle("connector_call", {"tool": tool, "arguments": arguments}, tool_use_id)

    def search(self, query="", app=None):
        args = {"query": query}
        if app:
            args["app"] = app
        return self.handle("connector_search", args)["toolResult"]

    def approve(self, approval):
        return approvals.decide(self.store, self.run["pk"], approval["approvalId"], approve=True)

    @property
    def executed(self):
        return self.transport.executed

    @property
    def last(self):
        return self.turn.steps[-1]


@pytest.fixture
def world(api_table, monkeypatch):
    return World(api_table, monkeypatch)
