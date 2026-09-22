"""Hop depth only counts if the trace survives the hop.

`collab.send` recognises a loop by counting prior messages in the same context
carrying the same `trace_id`. Every wake already recorded the trace it came from
on the new run (`trigger.traceId`) -- and nothing ever read it back. So every Bot
minted a fresh trace on every send, `hopCount` was 0 every time, and the hop
ceiling could not be reached by any sequence of real messages: A wakes B wakes A
wakes B ran until the round limit or the money ran out instead.

That was survivable while two Bots could only reach each other inside a task or
room they already shared. It stopped being survivable when any Bot could message
any teammate directly, which is what made this worth fixing rather than noting.

The chain these tests walk is the real one: send with priority, read the run that
wake created, send from *that* run, and so on.
"""

import pytest

import handlers.orchestrator as orch
from amazai import collab, keys as K

ENG = {"agentId": "eng", "name": "Engineering", "status": "active",
       "budget": {"maxConcurrentRuns": 8}}
OPS = {"agentId": "ops", "name": "Cloud Operations", "status": "active",
       "budget": {"maxConcurrentRuns": 8}}


@pytest.fixture(autouse=True)
def no_wake_invoke(monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_FN_ARN", raising=False)


@pytest.fixture
def agents(store):
    for row in (ENG, OPS):
        store.put({"pk": K.agent_pk(store.owner_id, row["agentId"]), "sk": "META", "entity": "Agent", **row})
    return store


def send(agents, sender, to, run, text="over to you", **args):
    """One hop, exactly as the loop makes it: the sender's own run, and a
    priority message so the recipient is woken and can answer."""
    return orch._message_agent(agents, run, sender,
                               {"to": to, "text": text, "priority": True, **args})


def woken_run(agents, agent_id):
    """The newest run a wake created for this Bot."""
    rows = [r for r in agents.query_index("gsi1", "gsi1pk", "RUNS", limit=100)
            if r["agentId"] == agent_id]
    return sorted(rows, key=lambda r: r["createdAt"])[-1]


def fresh_run():
    """A run that starts a conversation rather than continuing one."""
    return {"runId": "run_start", "trigger": {"type": "user"}}


class TestTheTraceSurvivesAWake:
    def test_a_first_message_starts_a_trace(self, agents):
        row = send(agents, ENG, "ops", fresh_run())["message"]
        assert row["traceId"].startswith("trace_")
        assert row["hopCount"] == 0

    def test_the_run_the_wake_created_carries_it(self, agents):
        trace = send(agents, ENG, "ops", fresh_run())["message"]["traceId"]
        assert woken_run(agents, "ops")["trigger"]["traceId"] == trace

    def test_and_the_reply_from_that_run_continues_it(self, agents):
        """The link that was missing. This assertion failed before the fix: the
        reply minted a second trace and counted its hop as the first."""
        trace = send(agents, ENG, "ops", fresh_run())["message"]["traceId"]
        reply = send(agents, OPS, "eng", woken_run(agents, "ops"), "back to you")["message"]
        assert reply["traceId"] == trace
        assert reply["hopCount"] == 1

    def test_a_conversation_nobody_woke_starts_its_own(self, agents):
        send(agents, ENG, "ops", fresh_run())
        other = send(agents, ENG, "ops", fresh_run(), "unrelated")["message"]
        assert other["hopCount"] == 0, "an ordinary one-off message must not inherit a hop count"


class TestPingPongIsStopped:
    def test_the_hop_ceiling_is_reachable_by_real_messages(self, agents):
        """A wakes B wakes A wakes B ... Before the fix this ran forever, bounded
        only by the round limit and the budget -- ceilings meant for other
        things. Now it is the loop guard that stops it."""
        run, sender, other = fresh_run(), ENG, OPS
        hops = []
        for _ in range(collab.DEFAULT_MAX_HOP_DEPTH):
            row = send(agents, sender, other["agentId"], run)["message"]
            hops.append(row["hopCount"])
            run = woken_run(agents, other["agentId"])
            sender, other = other, sender

        assert hops == [0, 1, 2]
        with pytest.raises(collab.MessagingError, match="this looks like a loop"):
            send(agents, sender, other["agentId"], run)

    def test_the_refusal_is_logged_like_any_other(self, agents):
        run, sender, other = fresh_run(), ENG, OPS
        for _ in range(collab.DEFAULT_MAX_HOP_DEPTH):
            send(agents, sender, other["agentId"], run)
            run = woken_run(agents, other["agentId"])
            sender, other = other, sender
        with pytest.raises(collab.MessagingError):
            send(agents, sender, other["agentId"], run)
        denied = [r for r in agents.query_index("gsi1", "gsi1pk", "MESSAGES")
                  if r["entity"] == "AgentMessageDenied"]
        assert [r["reason"] for r in denied] == ["max hop depth exceeded"]


class TestTheTraceIsNotTheModelsToChoose:
    def test_a_model_supplied_trace_cannot_reset_the_hop_count(self, agents):
        """`trace_id` is deliberately absent from message_agent's schema, but a
        model can put anything in a tool call. If that value were honoured, every
        Bot could clear its own hop count and the ceiling would be decorative."""
        trace = send(agents, ENG, "ops", fresh_run())["message"]["traceId"]
        reply = send(agents, OPS, "eng", woken_run(agents, "ops"),
                     trace_id="trace_i_made_this_up")["message"]
        assert reply["traceId"] == trace
        assert reply["hopCount"] == 1

    def test_it_cannot_escape_the_ceiling_by_renaming_the_trace(self, agents):
        run, sender, other = fresh_run(), ENG, OPS
        for i in range(collab.DEFAULT_MAX_HOP_DEPTH):
            send(agents, sender, other["agentId"], run, trace_id=f"invented-{i}")
            run = woken_run(agents, other["agentId"])
            sender, other = other, sender
        with pytest.raises(collab.MessagingError, match="this looks like a loop"):
            send(agents, sender, other["agentId"], run, trace_id="invented-again")


class TestARoomOpenedMidChainStaysInIt:
    def test_the_members_inherit_the_trace(self, agents):
        trace = send(agents, ENG, "ops", fresh_run())["message"]["traceId"]
        ops_run = woken_run(agents, "ops")
        orch._create_group_chat(agents, ops_run, OPS, {
            "title": "Look at this", "goal": "work out what is looping",
            "agentIds": ["eng"]})
        room_runs = [r for r in agents.query_index("gsi1", "gsi1pk", "RUNS", limit=100)
                     if (r.get("trigger") or {}).get("type") == "group_chat"]
        assert len(room_runs) == 2
        assert {r["trigger"]["traceId"] for r in room_runs} == {trace}

    def test_a_room_opened_cold_carries_no_trace(self, agents):
        orch._create_group_chat(agents, fresh_run(), ENG, {
            "title": "New work", "goal": "start something", "agentIds": ["ops"]})
        room_runs = [r for r in agents.query_index("gsi1", "gsi1pk", "RUNS", limit=100)
                     if (r.get("trigger") or {}).get("type") == "group_chat"]
        assert all("traceId" not in r["trigger"] for r in room_runs)
