"""A ceiling that counts the wrong rows is not a ceiling.

`MSG#` is not `collab`'s prefix. Operator messages, assistant replies, routine
notices and `threads.event` bookkeeping all share it, and none of them carry a
`traceId` or an `at`. The window read was sized by count and filtered afterwards,
which DynamoDB cannot honour -- it applies its own `Limit` first -- so in a thread
with enough ordinary conversation every row a ceiling was counting fell outside
the page. Hop depth counted zero. The direct-message window counted zero. The
priority-wake window counted zero. All three **failed open**, silently, and only
in the threads busy enough to need them.

The read is now a time range narrowed to `AgentMessage` rows, so what a ceiling
counts is what a ceiling sees. Each test here first proves the guard fires, then
buries it in ordinary chat and proves it still fires.
"""

import pytest

from amazai import collab, keys as K
from amazai.store import now_iso, ordered_suffix

ENG = {"agentId": "eng", "name": "Engineering", "status": "active",
       "budget": {"maxConcurrentRuns": 8}}
OPS = {"agentId": "ops", "name": "Cloud Operations", "status": "active",
       "budget": {"maxConcurrentRuns": 8}}


@pytest.fixture
def agents(store):
    for row in (ENG, OPS):
        store.put({"pk": K.agent_pk(row["agentId"]), "sk": "META", "entity": "Agent", **row})
    return store


def set_policy(store, cfg):
    store.put({"pk": K.user_pk(store.owner_id), "sk": "META", "entity": "User",
              "policy": {"messaging": cfg}})


def chatter(store, thread_id, count):
    """Ordinary conversation in the same thread: what the operator says, what a
    Bot replies, what the system notes. All `MSG#`, none of it agent-to-agent."""
    for i in range(count):
        store.put({
            "pk": K.thread_pk(thread_id), "sk": K.message_sk(now_iso(), ordered_suffix()),
            "entity": "Message", "role": "user" if i % 2 else "assistant",
            "author": "you" if i % 2 else "eng", "text": f"ordinary message {i}",
        })


def dm(agents, text="ping", **args):
    return collab.send(agents, sender_agent_id="eng", recipient_agent_id="ops",
                      args={"text": text, **args})


def thread_id():
    return collab.direct_thread_id("eng", "ops")


class TestHopDepthSurvivesABusyThread:
    def test_it_fires_in_a_quiet_thread(self, agents):
        for _ in range(collab.DEFAULT_MAX_HOP_DEPTH):
            dm(agents, trace_id="loop")
        with pytest.raises(collab.MessagingError, match="hop depth"):
            dm(agents, trace_id="loop")

    def test_it_still_fires_when_the_thread_is_full_of_conversation(self, agents):
        for _ in range(collab.DEFAULT_MAX_HOP_DEPTH):
            dm(agents, trace_id="loop")
        # Far more ordinary rows than any count-sized page would have held.
        chatter(agents, thread_id(), 200)
        with pytest.raises(collab.MessagingError, match="hop depth"):
            dm(agents, trace_id="loop")


class TestTheVolumeCeilingSurvivesABusyThread:
    def test_it_fires_in_a_quiet_thread(self, agents):
        set_policy(agents, {"maxDirectMessagesPerWindow": 3})
        for i in range(3):
            dm(agents, f"m{i}", trace_id=f"t{i}")
        with pytest.raises(collab.MessagingError, match="most allowed"):
            dm(agents, "over", trace_id="t-over")

    def test_it_still_fires_when_the_thread_is_full_of_conversation(self, agents):
        set_policy(agents, {"maxDirectMessagesPerWindow": 3})
        for i in range(3):
            dm(agents, f"m{i}", trace_id=f"t{i}")
        chatter(agents, thread_id(), 200)
        with pytest.raises(collab.MessagingError, match="most allowed"):
            dm(agents, "over", trace_id="t-over")


class TestPriorityThrottlingSurvivesABusyThread:
    def test_it_throttles_in_a_quiet_thread(self, agents):
        set_policy(agents, {"maxPriorityWakesPerWindow": 1})
        assert dm(agents, "a", priority=True, trace_id="p1")["priority_granted"] is True
        assert dm(agents, "b", priority=True, trace_id="p2")["priority_granted"] is False

    def test_it_still_throttles_when_the_thread_is_full_of_conversation(self, agents):
        set_policy(agents, {"maxPriorityWakesPerWindow": 1})
        assert dm(agents, "a", priority=True, trace_id="p1")["priority_granted"] is True
        chatter(agents, thread_id(), 200)
        throttled = dm(agents, "b", priority=True, trace_id="p2")
        assert throttled["priority_granted"] is False, "a granted wake fell outside the read"


class TestTheReadItself:
    def test_ordinary_conversation_is_not_counted_as_agent_traffic(self, agents):
        dm(agents, "the only agent message")
        chatter(agents, thread_id(), 50)
        context = collab.direct_context(agents, sender_id="eng", recipient_id="ops")
        rows = collab._agent_messages_since(agents, context, minutes=60)
        assert [r["text"] for r in rows] == ["the only agent message"]

    def test_the_denial_trail_is_not_counted_either(self, agents):
        """`MSGDENY#` sorts *after* `MSG#`, so an open-ended range read would
        collect the audit rows and count refusals as though they were sends."""
        set_policy(agents, {"maxDirectMessagesPerWindow": 1})
        dm(agents, "first")
        for _ in range(3):
            with pytest.raises(collab.MessagingError):
                dm(agents, "refused")
        context = collab.direct_context(agents, sender_id="eng", recipient_id="ops")
        rows = collab._agent_messages_since(agents, context, minutes=60)
        assert [r["entity"] for r in rows] == ["AgentMessage"]

    def test_a_message_older_than_the_window_falls_out_of_it(self, agents):
        context = collab.direct_context(agents, sender_id="eng", recipient_id="ops")
        agents.put({
            "pk": K.thread_pk(thread_id()),
            "sk": K.message_sk("2020-01-01T00:00:00Z", ordered_suffix()),
            "entity": "AgentMessage", "text": "last year", "at": "2020-01-01T00:00:00Z",
        })
        dm(agents, "now")
        rows = collab._agent_messages_since(agents, context, minutes=60)
        assert [r["text"] for r in rows] == ["now"]
