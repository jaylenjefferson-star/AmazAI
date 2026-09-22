"""Task/context-bound agent-to-agent messaging.

See docs/architecture/17-message-and-memory-authorization.md §1: every
message is bound to a task (a Run) or a collaboration context (a Thread),
both parties must be participants (or an org escalation policy applies),
hop depth and per-task message ceilings are hard stops, and `priority` only
ever requests -- never forces -- an expedited wake.
"""

import pytest

from amazai import collab, keys as K, runs

import handlers.orchestrator as orch

FROM_AGENT = {"agentId": "eng", "name": "Engineering", "status": "active",
             "budget": {"maxConcurrentRuns": 3}}
TO_AGENT = {"agentId": "ops", "name": "Cloud Operations", "status": "active",
           "budget": {"maxConcurrentRuns": 3}}
THIRD_AGENT = {"agentId": "clo", "name": "Counsel", "status": "active",
              "budget": {"maxConcurrentRuns": 3}}


def _set_org_policy(store, messaging_cfg: dict) -> None:
    """`store.update` needs a row to already exist; the test store never ran
    `identity.ensure_user`, so this upserts the whole USER#/META row rather
    than assuming one is there."""
    store.put({"pk": K.user_pk(store.owner_id), "sk": "META", "entity": "User",
              "policy": {"messaging": messaging_cfg}})


@pytest.fixture
def agents(store):
    store.put({"pk": K.agent_pk("eng"), "sk": "META", "entity": "Agent", **FROM_AGENT})
    store.put({"pk": K.agent_pk("ops"), "sk": "META", "entity": "Agent", **TO_AGENT})
    store.put({"pk": K.agent_pk("clo"), "sk": "META", "entity": "Agent", **THIRD_AGENT})
    return store


@pytest.fixture
def room(agents):
    """A collaboration context both eng and ops belong to."""
    agents.put({"pk": K.thread_pk("room-1"), "sk": "META", "entity": "Thread",
               "kind": "room", "agentIds": ["eng", "ops"]})
    return "room-1"


@pytest.fixture
def a_task(agents):
    """A Run only `eng` owns -- `ops` is not a participant by default."""
    run = runs.create(agents, agent_id="eng", thread_id="task-thread-1", goal="ship it")
    return run


class TestEveryMessageIsBoundToOneContext:
    def test_naming_no_task_and_no_room_is_a_direct_message(self, agents):
        """Not a refusal. A Bot that names neither is writing to one teammate,
        and the context that binds it is the obvious one -- the two of them."""
        result = collab.send(agents, sender_agent_id="eng", recipient_agent_id="ops",
                            args={"text": "hi"})
        row = result["message"]
        assert row["contextKind"] == collab.DIRECT
        assert row["collaborationContextId"] == collab.direct_thread_id("eng", "ops")
        assert row["taskId"] is None
        assert row["policyResult"]["allowed"] is True
        assert row["policyResult"]["escalated"] is False, "a direct message is not an escalation"

    def test_both_task_id_and_context_id_is_rejected(self, agents, room, a_task):
        """A message belongs to one conversation, so naming two is still nothing."""
        with pytest.raises(collab.MessagingError, match="not both"):
            collab.send(agents, sender_agent_id="eng", recipient_agent_id="ops",
                       args={"text": "hi", "task_id": a_task["runId"],
                             "collaboration_context_id": room})

    def test_a_context_cannot_be_resolved_out_of_nothing_at_all(self, agents):
        """The structural check survives: with no parties and no ids there is no
        conversation to bind to, and a message with no binding is the one thing
        this module exists to refuse."""
        with pytest.raises(collab.MessagingError):
            collab.resolve_context(agents)


class TestUnauthorizedCrossTaskMessaging:
    def test_recipient_not_a_participant_in_the_task_is_denied(self, agents, a_task):
        """`ops` never accepted a handoff on this task -- it is not a
        participant, and no org policy allows escalation, so this is a hard
        stop, not a silent drop."""
        with pytest.raises(collab.MessagingError):
            collab.send(agents, sender_agent_id="eng", recipient_agent_id="ops",
                       args={"text": "help me", "task_id": a_task["runId"]})

    def test_a_denied_send_is_still_logged_for_audit(self, agents, a_task):
        with pytest.raises(collab.MessagingError):
            collab.send(agents, sender_agent_id="eng", recipient_agent_id="ops",
                       args={"text": "help me", "task_id": a_task["runId"]})
        denied = [r for r in agents.query_index("gsi1", "gsi1pk", "MESSAGES")
                 if r["entity"] == "AgentMessageDenied"]
        assert len(denied) == 1
        assert denied[0]["senderAgentId"] == "eng"
        assert denied[0]["recipientAgentId"] == "ops"
        assert denied[0]["policyResult"]["allowed"] is False

    def test_org_escalation_policy_allows_the_same_send(self, agents, a_task):
        _set_org_policy(agents, {"allowCrossContextEscalation": True})
        result = collab.send(agents, sender_agent_id="eng", recipient_agent_id="ops",
                             args={"text": "help me", "task_id": a_task["runId"]})
        assert result["message"]["policyResult"]["escalated"] is True

    def test_room_participants_can_message_each_other(self, agents, room):
        result = collab.send(agents, sender_agent_id="eng", recipient_agent_id="ops",
                             args={"text": "status", "collaboration_context_id": room})
        assert result["message"]["policyResult"]["allowed"] is True
        assert result["message"]["collaborationContextId"] == room

    def test_a_non_member_of_the_room_is_denied(self, agents, room):
        with pytest.raises(collab.MessagingError):
            collab.send(agents, sender_agent_id="eng", recipient_agent_id="clo",
                       args={"text": "status", "collaboration_context_id": room})


class TestLoopTermination:
    def test_hop_depth_is_enforced_within_a_trace(self, agents, room):
        trace_id = "trace-loop"
        for _ in range(3):
            collab.send(agents, sender_agent_id="eng", recipient_agent_id="ops",
                       args={"text": "ping", "collaboration_context_id": room,
                             "trace_id": trace_id})
        with pytest.raises(collab.MessagingError, match="hop depth"):
            collab.send(agents, sender_agent_id="eng", recipient_agent_id="ops",
                       args={"text": "ping again", "collaboration_context_id": room,
                             "trace_id": trace_id})

    def test_a_fresh_trace_is_not_limited_by_another_traces_depth(self, agents, room):
        for _ in range(3):
            collab.send(agents, sender_agent_id="eng", recipient_agent_id="ops",
                       args={"text": "ping", "collaboration_context_id": room,
                             "trace_id": "trace-a"})
        # A brand new trace starts its own hop count from zero.
        result = collab.send(agents, sender_agent_id="eng", recipient_agent_id="ops",
                             args={"text": "ping", "collaboration_context_id": room,
                                   "trace_id": "trace-b"})
        assert result["message"]["hopCount"] == 0

    def test_message_ceiling_per_task_is_enforced(self, agents, room, monkeypatch):
        _set_org_policy(agents, {"maxMessagesPerTask": 2})
        collab.send(agents, sender_agent_id="eng", recipient_agent_id="ops",
                   args={"text": "one", "collaboration_context_id": room, "trace_id": "t1"})
        collab.send(agents, sender_agent_id="eng", recipient_agent_id="ops",
                   args={"text": "two", "collaboration_context_id": room, "trace_id": "t2"})
        with pytest.raises(collab.MessagingError, match="ceiling"):
            collab.send(agents, sender_agent_id="eng", recipient_agent_id="ops",
                       args={"text": "three", "collaboration_context_id": room, "trace_id": "t3"})


class TestPriorityWakeThrottling:
    def test_priority_requested_grants_up_to_the_window_limit(self, agents, room):
        _set_org_policy(agents, {"maxPriorityWakesPerWindow": 2})
        r1 = collab.send(agents, sender_agent_id="eng", recipient_agent_id="ops",
                         args={"text": "a", "collaboration_context_id": room,
                               "priority": True, "trace_id": "p1"})
        r2 = collab.send(agents, sender_agent_id="eng", recipient_agent_id="ops",
                         args={"text": "b", "collaboration_context_id": room,
                               "priority": True, "trace_id": "p2"})
        assert r1["priority_granted"] is True
        assert r2["priority_granted"] is True

    def test_priority_is_throttled_once_the_window_is_exhausted(self, agents, room):
        _set_org_policy(agents, {"maxPriorityWakesPerWindow": 1})
        collab.send(agents, sender_agent_id="eng", recipient_agent_id="ops",
                   args={"text": "a", "collaboration_context_id": room,
                         "priority": True, "trace_id": "p1"})
        throttled = collab.send(agents, sender_agent_id="eng", recipient_agent_id="ops",
                                args={"text": "b", "collaboration_context_id": room,
                                      "priority": True, "trace_id": "p2"})
        # Throttled, not denied: the message is still delivered as deferred.
        assert throttled["priority_granted"] is False
        assert throttled["message"]["priorityRequested"] is True

    def test_priority_never_bypasses_recipient_concurrency(self, agents, room):
        """Even a granted priority wake must still clear `may_wake_now` --
        it requests scheduling, it does not force execution."""
        agents.update(K.agent_pk("ops"), "META", {"budget": {"maxConcurrentRuns": 1}})
        for i in range(2):
            runs.create(agents, agent_id="ops", thread_id="other-thread",
                       goal=f"busy {i}")
        allowed, reason = collab.may_wake_now(
            agents, agents.get(K.agent_pk("ops"), "META"), collab.limits_for_org(agents))
        assert allowed is False
        assert "concurrency" in reason


class TestAuditVisibility:
    def test_every_send_leaves_one_row_on_the_messages_feed(self, agents, room, a_task):
        collab.send(agents, sender_agent_id="eng", recipient_agent_id="ops",
                   args={"text": "in the room", "collaboration_context_id": room})
        with pytest.raises(collab.MessagingError):
            collab.send(agents, sender_agent_id="eng", recipient_agent_id="ops",
                       args={"text": "unauthorized", "task_id": a_task["runId"]})
        feed = agents.query_index("gsi1", "gsi1pk", "MESSAGES")
        entities = sorted(r["entity"] for r in feed)
        assert entities == ["AgentMessage", "AgentMessageDenied"]

    def test_audit_row_carries_full_provenance(self, agents, room):
        result = collab.send(agents, sender_agent_id="eng", recipient_agent_id="ops",
                             args={"text": "status", "collaboration_context_id": room,
                                   "parent_message_id": "msg_parent",
                                   "parent_handoff_id": "hoff_parent"})
        row = result["message"]
        for field in ("senderAgentId", "recipientAgentId", "collaborationContextId",
                     "parentMessageId", "parentHandoffId", "hopCount", "traceId",
                     "policyResult"):
            assert field in row


class TestMessageAgentToolWiring:
    def test_message_agent_requires_to(self, agents, room):
        with pytest.raises(collab.MessagingError):
            orch._message_agent(agents, {"runId": "run_x"}, FROM_AGENT,
                               {"text": "hi", "collaboration_context_id": room})

    def test_messaging_yourself_is_rejected(self, agents, room):
        with pytest.raises(collab.MessagingError):
            orch._message_agent(agents, {"runId": "run_x"}, FROM_AGENT,
                               {"to": "eng", "text": "note to self",
                                "collaboration_context_id": room})

    def test_messaging_an_unknown_agent_is_rejected(self, agents, room):
        with pytest.raises(collab.MessagingError):
            orch._message_agent(agents, {"runId": "run_x"}, FROM_AGENT,
                               {"to": "nobody", "text": "hi",
                                "collaboration_context_id": room})

    def test_messaging_an_inactive_agent_is_rejected(self, agents, room):
        agents.update(K.agent_pk("ops"), "META", {"status": "paused"})
        with pytest.raises(collab.MessagingError):
            orch._message_agent(agents, {"runId": "run_x"}, FROM_AGENT,
                               {"to": "ops", "text": "hi",
                                "collaboration_context_id": room})

    def test_a_deferred_message_does_not_wake_a_run(self, agents, room):
        result = orch._message_agent(agents, {"runId": "run_x"}, FROM_AGENT,
                                     {"to": "ops", "text": "later",
                                      "collaboration_context_id": room})
        assert result["priorityGranted"] is False
        assert result["woke"] is False
        runs_for_ops = [r for r in agents.query_index("gsi1", "gsi1pk", "RUNS")
                        if r["agentId"] == "ops"]
        assert runs_for_ops == []

    def test_a_priority_message_wakes_a_run_for_the_recipient(self, agents, room, monkeypatch):
        monkeypatch.delenv("ORCHESTRATOR_FN_ARN", raising=False)
        result = orch._message_agent(agents, {"runId": "run_x"}, FROM_AGENT,
                                     {"to": "ops", "text": "urgent",
                                      "collaboration_context_id": room, "priority": True})
        assert result["priorityGranted"] is True
        assert result["woke"] is True
        runs_for_ops = [r for r in agents.query_index("gsi1", "gsi1pk", "RUNS")
                        if r["agentId"] == "ops"]
        assert len(runs_for_ops) == 1
        assert runs_for_ops[0]["trigger"]["type"] == "agent"
        assert runs_for_ops[0]["trigger"]["fromAgentId"] == "eng"
