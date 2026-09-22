"""A Bot can ask one teammate one thing.

Before this, it could not. `message_agent` demanded a task_id or a
collaboration_context_id, and a Bot that shared neither with the teammate it
wanted was refused outright:

    blocked: message_agent requires exactly one of task_id or
    collaboration_context_id

The only way through was `create_group_chat` -- which starts a run for every
member -- so "ask Ledger to confirm the Q3 numbers" cost a room and six turns,
or it cost a denied step and nothing at all.

What is proved here is that the *binding* was the point, not the hoop: a direct
message still lands in a real, readable, two-participant context, is still
logged, still hop-limited, still rate-limited, and still cannot reach a Bot that
does not exist or is not running. The context is simply opened instead of
demanded.
"""

import pytest

from amazai import collab, keys as K, runs

import handlers.orchestrator as orch

ENG = {"agentId": "eng", "name": "Engineering", "status": "active",
       "budget": {"maxConcurrentRuns": 3}}
OPS = {"agentId": "ops", "name": "Cloud Operations", "status": "active",
       "budget": {"maxConcurrentRuns": 3}}
CLO = {"agentId": "clo", "name": "Counsel", "status": "active",
       "budget": {"maxConcurrentRuns": 3}}


@pytest.fixture
def agents(store):
    for row in (ENG, OPS, CLO):
        store.put({"pk": K.agent_pk(store.owner_id, row["agentId"]), "sk": "META", "entity": "Agent", **row})
    return store


def set_org_policy(store, messaging_cfg: dict) -> None:
    store.put({"pk": K.user_pk(store.owner_id), "sk": "META", "entity": "User",
              "policy": {"messaging": messaging_cfg}})


def dm(agents, text="can you confirm the Q3 numbers?", sender="eng", to="ops", **args):
    return collab.send(agents, sender_agent_id=sender, recipient_agent_id=to,
                      args={"text": text, **args})


def thread(agents, sender="eng", to="ops"):
    return agents.try_get(K.thread_pk(collab.direct_thread_id(sender, to)), "META")


class TestTheConversationTwoBotsShare:
    def test_it_is_the_same_conversation_whichever_of_them_writes_first(self, agents):
        assert collab.direct_thread_id("eng", "ops") == collab.direct_thread_id("ops", "eng")

    def test_two_different_pairs_do_not_share_one(self, agents):
        assert collab.direct_thread_id("eng", "ops") != collab.direct_thread_id("eng", "clo")

    def test_the_first_message_opens_it(self, agents):
        assert thread(agents) is None
        dm(agents)
        row = thread(agents)
        assert row["agentIds"] == ["eng", "ops"]
        assert row["direct"] is True and row["status"] == "active"
        assert row["createdBy"] == "agent:eng"

    def test_it_is_reused_rather_than_opened_again(self, agents):
        dm(agents, "first")
        opened = thread(agents)["createdAt"]
        dm(agents, "second", sender="ops", to="eng")          # the other direction
        assert thread(agents)["createdAt"] == opened
        rows = [r for r in agents.query_index("gsi1", "gsi1pk", "THREADS")
                if r.get("direct")]
        assert len(rows) == 1

    def test_both_messages_land_in_it(self, agents):
        dm(agents, "first")
        dm(agents, "second", sender="ops", to="eng")
        said = [m["text"] for m in agents.query(
            K.thread_pk(collab.direct_thread_id("eng", "ops")), sk_prefix="MSG#")]
        assert said == ["first", "second"]

    def test_the_operator_can_read_it(self, agents):
        """Not a private channel. A conversation between two Bots that nobody
        can read is the one thing `_create_group_chat` already refuses to make."""
        dm(agents)
        listed = agents.query_index("gsi1", "gsi1pk", "THREADS")
        assert [t["threadId"] for t in listed] == [collab.direct_thread_id("eng", "ops")]
        assert listed[0]["title"] == "Engineering & Cloud Operations"

    def test_no_run_is_started_for_anyone(self, agents):
        """The difference from a group chat, and the reason to reach for this:
        asking a question costs a message, not a turn for every member."""
        dm(agents)
        assert agents.query_index("gsi1", "gsi1pk", "RUNS") == []


class TestTheBoundaryIsUnchanged:
    def test_a_direct_message_is_authorised_not_escalated(self, agents):
        row = dm(agents)["message"]
        assert row["policyResult"] == {"allowed": True,
                                       "reason": "both participants in context",
                                       "escalated": False}

    def test_it_is_on_the_audit_feed_like_every_other_send(self, agents):
        dm(agents)
        feed = agents.query_index("gsi1", "gsi1pk", "MESSAGES")
        assert [r["entity"] for r in feed] == ["AgentMessage"]
        assert feed[0]["contextKind"] == collab.DIRECT

    def test_a_loop_is_still_stopped_by_hop_depth(self, agents):
        for _ in range(collab.DEFAULT_MAX_HOP_DEPTH):
            dm(agents, "ping", trace_id="loop")
        with pytest.raises(collab.MessagingError, match="hop depth"):
            dm(agents, "ping again", trace_id="loop")

    def test_a_runaway_pair_is_stopped_by_a_window_not_a_lifetime(self, agents):
        """The per-task ceiling cannot apply as written to a conversation that
        never ends: two Bots would fall silent for good. It guards a window."""
        set_org_policy(agents, {"maxDirectMessagesPerWindow": 2})
        dm(agents, "one", trace_id="t1")
        dm(agents, "two", trace_id="t2")
        with pytest.raises(collab.MessagingError, match="last 60 minutes"):
            dm(agents, "three", trace_id="t3")

    def test_a_refusal_is_logged_with_the_pair_it_refused(self, agents):
        set_org_policy(agents, {"maxDirectMessagesPerWindow": 1})
        dm(agents, "one", trace_id="t1")
        with pytest.raises(collab.MessagingError):
            dm(agents, "two", trace_id="t2")
        denied = [r for r in agents.query_index("gsi1", "gsi1pk", "MESSAGES")
                  if r["entity"] == "AgentMessageDenied"]
        assert len(denied) == 1
        assert (denied[0]["senderAgentId"], denied[0]["recipientAgentId"]) == ("eng", "ops")
        assert denied[0]["contextKind"] == collab.DIRECT

    def test_priority_is_still_only_a_request(self, agents):
        set_org_policy(agents, {"maxPriorityWakesPerWindow": 1})
        assert dm(agents, "a", priority=True, trace_id="p1")["priority_granted"] is True
        throttled = dm(agents, "b", priority=True, trace_id="p2")
        assert throttled["priority_granted"] is False
        assert throttled["message"]["priorityRequested"] is True


class TestThroughTheToolTheModelCalls:
    def send(self, agents, **args):
        return orch._message_agent(agents, {"runId": "run_x"}, ENG, {"to": "ops", **args})

    def test_to_and_text_are_all_it_needs(self, agents):
        result = self.send(agents, text="can you confirm the Q3 numbers?")
        assert result["context"].kind == collab.DIRECT
        assert result["recipientName"] == "Cloud Operations"
        assert result["woke"] is False

    def test_a_priority_direct_message_wakes_the_recipient_on_that_conversation(
            self, agents, monkeypatch):
        monkeypatch.delenv("ORCHESTRATOR_FN_ARN", raising=False)
        result = self.send(agents, text="the build is red", priority=True)
        assert result["woke"] is True
        woken = [r for r in agents.query_index("gsi1", "gsi1pk", "RUNS")
                 if r["agentId"] == "ops"]
        assert len(woken) == 1
        assert woken[0]["threadId"] == collab.direct_thread_id("eng", "ops")
        assert woken[0]["trigger"]["fromAgentId"] == "eng"
        assert woken[0]["goal"] == "the build is red"

    def test_a_bot_that_does_not_exist_is_still_refused(self, agents):
        with pytest.raises(collab.MessagingError, match="no such active recipient"):
            orch._message_agent(agents, {"runId": "run_x"}, ENG,
                                {"to": "nobody", "text": "hi"})

    def test_a_paused_bot_is_still_refused(self, agents):
        agents.update(K.agent_pk(agents.owner_id, "ops"), "META", {"status": "paused"})
        with pytest.raises(collab.MessagingError):
            self.send(agents, text="hi")

    def test_a_bot_still_cannot_message_itself(self, agents):
        with pytest.raises(collab.MessagingError, match="cannot message itself"):
            orch._message_agent(agents, {"runId": "run_x"}, ENG, {"to": "eng", "text": "hi"})

    def test_it_still_needs_something_to_say(self, agents):
        with pytest.raises(collab.MessagingError, match="requires 'to'"):
            orch._message_agent(agents, {"runId": "run_x"}, ENG, {"text": "hi"})


class TestNamingASharedContextStillSendsIntoIt:
    def test_a_room_message_goes_to_the_room_not_to_a_direct_conversation(self, agents):
        agents.put({"pk": K.thread_pk("room-1"), "sk": "META", "entity": "Thread",
                    "kind": "room", "agentIds": ["eng", "ops"]})
        row = dm(agents, "status", collaboration_context_id="room-1")["message"]
        assert row["contextKind"] == "room"
        assert row["collaborationContextId"] == "room-1"
        assert thread(agents) is None, "a room message opened a direct conversation too"

    def test_a_task_message_still_needs_the_recipient_to_be_in_that_task(self, agents):
        """Naming a task is a claim about who is in it, and that claim is still
        checked. The direct conversation is a different context, not a bypass."""
        task = runs.create(agents, agent_id="eng", thread_id="task-thread-1", goal="ship it")
        with pytest.raises(collab.MessagingError):
            dm(agents, "help me", task_id=task["runId"])
