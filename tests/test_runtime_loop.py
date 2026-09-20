"""The agent loop, driven through the real `_handle_tool`.

These tests exist for one claim, and it is the one the whole product rests on:
**enforcement is code, never prompt.** A connector write on the always-approve
floor must stop for a person whether or not the model remembered to ask first;
an approval must be spent once and only on the arguments it was given; and every
step must carry the verdict and the rule behind it.

Only the network hop to Pipedream is stubbed. The store, the policy engine, the
router, the approvals and the orchestrator's own handler are the real ones.
"""

import types

import pytest

import handlers.api as api
import handlers.orchestrator as orch
from amazai import (agentcore, approvals, connectors as C, keys as K, policy,
                    review, router, runs, threads)
from amazai.cost import RunCost
from amazai.evidence import EvidenceWriter
from amazai.push import Push
from amazai.states import RunState
from amazai.store import Store

from tests.test_agents_api import api_table, call  # noqa: F401

SLACK = "pipedream:slack"


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


class Proxy:
    def __init__(self):
        self.calls = []

    def proxy(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True}


def tool_call(name, args, tool_use_id="tu-1"):
    return types.SimpleNamespace(tool_name=name, tool_input=args, tool_use_id=tool_use_id)


class World:
    """One agent holding read and post on Slack, one run, one turn."""

    def __init__(self, table, monkeypatch):
        self.store = Store("owner-a", table=table)
        call("POST", f"/connectors/{SLACK}/install", {"accountId": "apn_live123"})
        status, agent = call("POST", "/agents", {
            "name": "Comms", "role": "Reads and posts in Slack.", "modelTier": "balanced",
            "avatar": {"shape": "cloud", "color": "#12a594"},
            "budget": {"perRunUsd": 1.0, "perMonthUsd": 10.0},
            "grants": [{"connectorId": SLACK, "allowedTools": ["slack.read", "slack.post"]}],
        })
        assert status == 201, agent
        self.agent_id = agent["agentId"]
        self.agent = self.store.get(K.agent_pk(self.agent_id), "META")
        self.run = runs.create(self.store, agent_id=self.agent_id,
                               thread_id=f"dm-{self.agent_id}", goal="post the update")
        self.push, self.ev, self.cost, self.turn = RecPush(), EvidenceWriter("run_test"), RunCost(), orch.Turn()
        self.proxy = Proxy()
        monkeypatch.setattr(orch, "_pipedream_client", lambda: self.proxy)
        self.resolution = router.resolve_tools(router.ResolutionInput(
            agent_allowed_tools=frozenset(self.agent["allowedTools"]),
            grants=C.router_grants(self.store, self.agent_id)))
        self.seq = 0

    def handle(self, name, args, tool_use_id="tu-1"):
        self.seq += 1
        return orch._handle_tool(self.store, self.run, self.agent, self.ev, self.push,
                                 self.resolution, tool_call(name, args, tool_use_id),
                                 self.seq, self.cost, self.turn)

    def approve(self, approval):
        return approvals.decide(self.store, self.run["pk"], approval["approvalId"], approve=True)

    @property
    def last(self):
        return self.turn.steps[-1]


@pytest.fixture
def world(api_table, monkeypatch):
    return World(api_table, monkeypatch)


POST = {"channel": "C123", "text": "Shipped."}


class TestAConnectorWriteStopsForAPersonInCode:
    """The gap this closes: `connectors.invoke` says `policy.evaluate` already ran.
    It had not. A model that skipped `request_approval` ran `slack.post`."""

    def test_a_read_runs_and_says_why(self, world):
        result = world.handle("slack.read", {"channel": "C123"})
        assert result["pause"] is False
        assert len(world.proxy.calls) == 1
        assert world.last["review"]["decision"] == review.ALLOWED
        assert world.last["review"]["rule"] == "read"

    def test_a_post_the_model_never_asked_about_does_not_run(self, world):
        result = world.handle("slack.post", POST)
        assert result["pause"] is True
        assert world.proxy.calls == [], "the write reached Slack without approval"
        assert world.last["review"]["decision"] == review.ASKED

    def test_the_approval_names_the_rule_that_stopped_it(self, world):
        approval = world.handle("slack.post", POST)["approval"]
        card = approvals.to_card(approval)
        assert card["policy"]["rule"] == "floor"
        assert card["policy"]["matched"] == "slack.post"
        assert card["arguments"] == POST

    def test_once_approved_the_same_call_runs(self, world):
        approval = world.handle("slack.post", POST)["approval"]
        world.approve(approval)
        result = world.handle("slack.post", POST, tool_use_id="tu-2")
        assert result["pause"] is False
        assert len(world.proxy.calls) == 1
        assert world.last["review"]["decision"] == review.ALLOWED
        assert world.last["review"]["rule"] == "approved"
        assert world.last["review"]["matched"] == approval["approvalId"]

    def test_an_approval_is_spent_once(self, world):
        approval = world.handle("slack.post", POST)["approval"]
        world.approve(approval)
        world.handle("slack.post", POST, tool_use_id="tu-2")
        again = world.handle("slack.post", POST, tool_use_id="tu-3")
        assert again["pause"] is True, "an approval authorised a second post"
        assert len(world.proxy.calls) == 1

    def test_an_approval_does_not_cover_different_arguments(self, world):
        approval = world.handle("slack.post", POST)["approval"]
        world.approve(approval)
        other = world.handle("slack.post", {"channel": "C123", "text": "Something else."},
                             tool_use_id="tu-2")
        assert other["pause"] is True
        assert world.proxy.calls == []

    def test_a_denied_approval_does_not_unlock_the_call(self, world):
        approval = world.handle("slack.post", POST)["approval"]
        approvals.decide(world.store, world.run["pk"], approval["approvalId"], approve=False)
        assert world.handle("slack.post", POST, tool_use_id="tu-2")["pause"] is True
        assert world.proxy.calls == []

    def test_a_pre_approved_rule_cannot_lower_the_floor(self, world):
        world.agent = {**world.agent, "preapproved": ["slack.post"]}
        result = world.handle("slack.post", POST)
        assert result["pause"] is True, "a pre-approved rule overrode the always-approve floor"
        assert world.proxy.calls == []


class TestEveryStepCarriesAVerdict:
    def test_the_verdict_travels_with_the_pushed_step(self, world):
        world.handle("slack.read", {"channel": "C123"})
        pushed = world.push.steps[-1]
        assert pushed["review"]["decision"] == "allowed"
        assert pushed["name"] == "slack.read"

    def test_a_sandbox_tool_is_labelled_honestly_as_observed_not_gated(self, world):
        world.resolution = router.resolve_tools(router.ResolutionInput(
            agent_allowed_tools=frozenset({"shell"}), grants=[]))
        world.handle("shell", {"command": "ls"})
        assert world.last["review"]["rule"] == "sandbox"
        assert "sandbox" in world.last["review"]["reason"]

    def test_a_tool_without_a_grant_is_denied_and_says_so(self, world):
        world.handle("slack.delete_channel", {"channel": "C1"})
        assert world.last["review"]["decision"] == review.DENIED
        assert world.last["review"]["rule"] == "no_grant"

    def test_a_never_approvable_request_is_denied_with_its_rule(self, world):
        world.handle("request_approval", {"action": "aws.admin_credential",
                                          "arguments": {}, "why": "x"})
        assert world.last["review"]["decision"] == review.DENIED
        assert world.last["review"]["rule"] == "never"
        assert world.last["review"]["matched"] == "aws.admin_credential"

    def test_a_requested_approval_for_a_floor_tool_names_the_floor(self, world):
        approval = world.handle("request_approval", {
            "action": "email.send", "arguments": {"to": "x@example.com"}, "why": "reply"})["approval"]
        assert approval["policy"] == {
            "rule": "floor", "matched": "email.send",
            "reason": "on the always-approve floor"}
        assert world.last["review"]["decision"] == review.ASKED

    def test_a_handoff_says_no_access_travels_with_it(self, world):
        world.handle("handoff", {"to": "ops", "goal": "g", "state": "s", "requestedAction": "a"})
        assert "no access" in world.last["review"]["reason"]

    def test_the_verdict_is_stored_on_the_runs_own_event(self, world):
        world.handle("slack.read", {"channel": "C123"})
        events = world.store.query(world.run["pk"], sk_prefix="EVT#")
        assert events[-1]["review"]["decision"] == "allowed"


class TestCardsComeFromToolCalls:
    def test_a_connector_the_agent_lacks_becomes_a_connect_card(self, world):
        world.handle("request_connector", {"connectorId": "pipedream:nonexistent", "why": "x"})
        assert world.turn.cards == []
        assert world.last["review"]["decision"] == review.DENIED

    def test_an_already_connected_connector_asks_for_nothing(self, world):
        world.handle("request_connector", {"connectorId": SLACK, "why": "to post"})
        assert world.turn.cards == []
        assert "already connected" in world.last["summary"]

    def test_a_connector_not_yet_granted_becomes_a_card(self, api_table, monkeypatch):
        # A fresh agent with no grants: the catalog has Slack, the agent does not.
        store = Store("owner-a", table=api_table)
        status, agent = call("POST", "/agents", {
            "name": "Bare", "role": "Has nothing yet.", "modelTier": "balanced",
            "avatar": {"shape": "cloud", "color": "#12a594"},
            "budget": {"perRunUsd": 1.0, "perMonthUsd": 10.0}})
        assert status == 201
        run = runs.create(store, agent_id=agent["agentId"], thread_id="dm-bare", goal="x")
        turn = orch.Turn()
        orch._handle_tool(store, run, store.get(K.agent_pk("bare"), "META"), EvidenceWriter("r"),
                          RecPush(), router.resolve_tools(router.ResolutionInput(
                              agent_allowed_tools=frozenset(), grants=[])),
                          tool_call("request_connector", {"connectorId": SLACK,
                                                          "why": "To read the channel."}),
                          1, RunCost(), turn)
        assert turn.cards == [{"type": "connect", "connectorId": SLACK,
                               "name": C.CATALOG[SLACK].name, "why": "To read the channel."}]

    def test_a_routine_proposal_becomes_a_card_and_creates_nothing(self, world):
        world.handle("propose_routine", {"name": "Weekday planning", "schedule": "weekday-9",
                                         "prompt": "Draft the day."})
        assert world.turn.cards == [{"type": "routine", "name": "Weekday planning",
                                     "prompt": "Draft the day.", "preset": "weekday-9",
                                     "agentId": world.agent_id}]
        assert call("GET", "/routines")[1]["routines"] == []

    def test_a_routine_proposal_cannot_carry_an_expression(self, world):
        world.handle("propose_routine", {"name": "Too clever", "prompt": "x",
                                         "schedule": "cron(* * * * ? *)"})
        assert world.turn.cards == []
        assert world.last["review"]["decision"] == review.DENIED

    def test_a_bot_proposal_is_an_approval_not_a_card(self, world):
        result = world.handle("propose_agent", {
            "name": "Calendar", "role": "Keeps the calendar.", "why": "A separate lane."})
        assert result["pause"] is True
        assert result["approval"]["action"] == "agent.create"
        assert result["approval"]["policy"]["rule"] == "floor"
        assert result["approval"]["policy"]["matched"] == "agent.create"

    def test_a_skill_proposal_is_an_approval_naming_its_rule(self, world):
        result = world.handle("propose_skill", {
            "name": "Weekly plan", "description": "Plans the week.", "body": "Steps...",
            "why": "Reusable."})
        assert result["approval"]["action"] == "skill.create"
        assert result["approval"]["policy"]["matched"] == "skill.create"


class TestWhatTheTurnLeavesBehind:
    def test_steps_and_cards_are_stored_on_the_message(self, world):
        world.handle("slack.read", {"channel": "C123"})
        world.handle("propose_routine", {"name": "Digest", "schedule": "daily-730", "prompt": "Summarise."})
        orch._persist_message(world.store, world.run, world.agent, "Here you go.", world.cost,
                              steps=world.turn.steps, cards=world.turn.cards,
                              started_at="2026-09-20T09:00:00Z")
        rows = world.store.query(K.thread_pk(world.run["threadId"]), sk_prefix="MSG#")
        row = rows[-1]
        assert [s["name"] for s in row["steps"]] == ["slack.read", "propose_routine"]
        assert row["steps"][0]["review"]["decision"] == "allowed"
        assert row["cards"][0]["type"] == "routine"
        assert row["startedAt"] == "2026-09-20T09:00:00Z" and row["endedAt"]

    def test_a_turn_with_no_words_still_keeps_its_trail_and_the_model_never_sees_it(self, world):
        world.handle("slack.post", POST)   # pauses; the model said nothing
        orch._persist_message(world.store, world.run, world.agent, "", world.cost,
                              steps=world.turn.steps, cards=[])
        rows = world.store.query(K.thread_pk(world.run["threadId"]), sk_prefix="MSG#")
        assert rows[-1]["steps"][0]["review"]["decision"] == "asked"
        assert agentcore.build_messages(rows) == []

    def test_saving_to_memory_writes_the_fact_and_a_real_event(self, world):
        world.handle("remember", {"scope": "agent", "title": "Prefers bullets",
                                  "body": "Summaries as bullets, not prose."})
        memories = world.store.query(K.agent_pk(world.agent_id), sk_prefix="MEM#")
        assert [m["title"] for m in memories] == ["Prefers bullets"]
        rows = world.store.query(K.thread_pk(world.run["threadId"]), sk_prefix="MSG#")
        event = rows[-1]
        assert event["kind"] == "event" and event["role"] == "system"
        assert "Prefers bullets" in event["text"]

    def test_an_event_is_not_sent_to_the_model_and_does_not_make_a_thread_unread(self, world):
        thread = K.thread_pk(world.run["threadId"])
        world.store.put({"pk": thread, "sk": "META", "entity": "Thread",
                         "threadId": world.run["threadId"], "lastActivity": "2026-01-01T00:00:00Z"})
        threads.event(world.store, world.run["threadId"], "Routine created: X", icon="clock")
        assert world.store.get(thread, "META")["lastActivity"] == "2026-01-01T00:00:00Z"
        rows = world.store.query(thread, sk_prefix="MSG#")
        assert agentcore.build_messages(rows) == []


class TestAPausedOrRunningRunCanBeStopped:
    def _executing(self, world):
        run = runs.advance(world.store, world.run, RunState.PLANNING)
        return runs.advance(world.store, run, RunState.EXECUTING)

    def test_a_redirect_stops_the_run_and_waits_for_it_to_end(self, world):
        run = self._executing(world)
        stopped = api._stop_run(world.store, run, redirect_text="Do B instead")
        assert stopped["state"] == RunState.CANCELLING.value
        assert world.store.get(run["pk"], "META")["redirect"]["text"] == "Do B instead"
        # No second run beside the first: two on one session would interleave.
        assert len(world.store.query_index("gsi1", "gsi1pk", "RUNS")) == 1

    def test_when_the_old_run_has_ended_the_redirect_starts(self, world):
        run = self._executing(world)
        api._stop_run(world.store, run, redirect_text="Do B instead")
        new = orch._chain_redirect(world.store, run["pk"], world.agent)
        assert new["goal"] == "Do B instead"
        assert world.store.get(run["pk"], "META")["redirectedTo"] == new["runId"]
        assert new["trigger"]["redirectOf"] == run["runId"]

    def test_a_redirect_starts_only_one_run(self, world):
        run = self._executing(world)
        api._stop_run(world.store, run, redirect_text="Do B instead")
        first = orch._chain_redirect(world.store, run["pk"], world.agent)
        assert first is not None
        assert orch._chain_redirect(world.store, run["pk"], world.agent) is None

    def test_stopping_a_run_that_is_waiting_on_an_approval_actually_settles_it(self, world, monkeypatch):
        """Before: the stop flagged it CANCELLING and nothing was watching, so it
        stayed there for good -- the state a stop most needs to work in."""
        invoked = []
        monkeypatch.setattr(api, "_invoke_orchestrator",
                            lambda *a, **k: invoked.append(k))
        approval = world.handle("slack.post", POST)["approval"]
        run = self._executing(world)
        run = runs.pause_for_approval(world.store, run, approval)

        api._stop_run(world.store, run)

        assert invoked == [{"cancel": True}], "nobody was told to settle the paused run"
        assert world.store.get(run["pk"], approvals_sk(approval))["status"] == approvals.DENIED
        out = orch._settle_paused_cancel(world.store, world.store.get(run["pk"], "META"))
        assert out["state"] == RunState.CANCELLED.value
        assert world.store.get(run["pk"], "META")["state"] == RunState.CANCELLED.value

    def test_stopping_twice_is_harmless(self, world):
        run = self._executing(world)
        first = api._stop_run(world.store, run)
        assert api._stop_run(world.store, first)["state"] == RunState.CANCELLING.value


def approvals_sk(approval):
    return K.approval_sk(approval["approvalId"])
