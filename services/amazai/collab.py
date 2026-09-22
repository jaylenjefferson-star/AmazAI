"""Authorization boundary for agent-to-agent messaging.

Implements the stricter half of `docs/architecture/17-message-and-memory-
authorization.md` §1. A message is never open broadcast: it is bound to a
task (a Run), a collaboration context (a Thread), or the direct conversation
the two parties share, and the sender and recipient must both be participants
in that context unless an org policy explicitly allows escalation.
`priority: true` only ever requests an expedited wake -- it never buys its way
past a hop-depth, rate, concurrency or budget ceiling; see `send()` and
`may_wake_now()`.

A binding is what makes a message answerable for, not a hoop to clear. When a
Bot names no task and no room, the context it needs is the obvious one -- itself
and the teammate it is writing to -- so `direct_context` opens it rather than
refusing the message. Every check that applied before still applies to it.

Every send, allowed or denied, leaves a row under the context's own
partition tagged `gsi1pk: MESSAGES`, so the full trail -- who asked to talk
to whom, in what task, and whether policy allowed it -- is one query away
regardless of whether the message was actually delivered.
"""

from __future__ import annotations

import hashlib

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from amazai import keys as K
from amazai.states import TERMINAL
from amazai.store import Store, new_id, now_iso, ordered_suffix

#: Mirrors the handoff loop-prevention depth in
#: docs/architecture/09-multi-agent.md -- one shared notion of "too deep".
#: Most agents one room holds. Past this a room stops being a conversation and
#: becomes a broadcast, and every participant's turn is a run someone pays for.
MAX_ROOM_MEMBERS = 6

#: A direct conversation between two Bots -- the third kind of context, opened on
#: demand by `direct_context`. A Bot that shared no task or room with a teammate
#: used to have nowhere to put a message: `message_agent` refused it outright, and
#: the only way through was to open a whole group chat and start a run for every
#: member of it in order to ask one teammate one question. The boundary is
#: unchanged -- a message is still bound to a context both parties belong to, still
#: logged, still hop- and rate-limited. There is simply now a context for two.
DIRECT = "direct"

DEFAULT_MAX_HOP_DEPTH = 3
DEFAULT_MAX_MESSAGES_PER_TASK = 200
DEFAULT_MAX_PRIORITY_WAKES_PER_WINDOW = 5
DEFAULT_PRIORITY_WINDOW_MINUTES = 60
DEFAULT_MAX_CONCURRENT_RUNS_PER_AGENT = 3

#: A direct conversation has no end, so the per-task ceiling cannot apply to it as
#: written: two Bots would fall silent for good on their two-hundredth message. The
#: ceiling is a runaway guard, so for a direct conversation it guards a window.
DEFAULT_MAX_DIRECT_MESSAGES_PER_WINDOW = 60
DEFAULT_DIRECT_WINDOW_MINUTES = 60

_TERMINAL_VALUES = frozenset(s.value for s in TERMINAL)


class MessagingError(PermissionError):
    """Raised for a structural or authorization failure.

    Deliberately a hard stop rather than a silent drop: an unbound message
    (no task_id/collaboration_context_id), a cross-context send with no
    escalation policy, a hop-depth or message-ceiling breach all raise here,
    and `orchestrator._handle_tool` turns that into a visible tool error
    rather than continuing as if nothing happened.
    """


@dataclass(frozen=True)
class Context:
    kind: str                    # "task" | "room" | "direct"
    context_id: str              # a runId (task) or a threadId (room, direct)
    thread_id: str               # where message history actually lives
    participants: frozenset[str]


@dataclass(frozen=True)
class PolicyResult:
    allowed: bool
    reason: str
    escalated: bool = False

    def to_item(self) -> dict:
        return {"allowed": self.allowed, "reason": self.reason, "escalated": self.escalated}


@dataclass(frozen=True)
class MessagingLimits:
    max_hop_depth: int = DEFAULT_MAX_HOP_DEPTH
    max_messages_per_task: int = DEFAULT_MAX_MESSAGES_PER_TASK
    max_priority_wakes_per_window: int = DEFAULT_MAX_PRIORITY_WAKES_PER_WINDOW
    priority_window_minutes: int = DEFAULT_PRIORITY_WINDOW_MINUTES
    max_concurrent_runs_per_agent: int = DEFAULT_MAX_CONCURRENT_RUNS_PER_AGENT
    max_direct_messages_per_window: int = DEFAULT_MAX_DIRECT_MESSAGES_PER_WINDOW
    direct_window_minutes: int = DEFAULT_DIRECT_WINDOW_MINUTES


def _context_pk(context: Context) -> str:
    """Message history for both kinds of context lives in a Thread row: a
    `room` context's own thread, or the task's owning run's thread -- so a
    priority-woken recipient's run (spawned onto that same threadId) sees
    the conversation with no separate lookup, and the console's task-bound
    room is just this thread."""
    return K.thread_pk(context.thread_id)


def direct_thread_id(one: str, other: str) -> str:
    """The id of the one direct conversation these two Bots share.

    Derived from who they are rather than looked up, so it is the same id every
    time without a scan, and so two Bots messaging each other in the same second
    cannot open two conversations between them.
    """
    pair = "|".join(sorted([one, other]))
    return "th_dm_" + hashlib.sha256(pair.encode()).hexdigest()[:24]


def direct_context(store: Store, *, sender_id: str, recipient_id: str) -> Context:
    """The two-Bot context a direct message binds to, opened if it is new.

    Deliberately a real, listed Thread. A private channel between two Bots that
    the operator cannot read is the one thing `_create_group_chat` already
    refuses to create, and nothing about a conversation having two participants
    instead of three makes it less worth reading. What it is *not* is a group
    chat: no run is started for anyone here, so asking a teammate a question
    costs one message rather than a turn for every member of a room.
    """
    thread_id = direct_thread_id(sender_id, recipient_id)
    pk = K.thread_pk(thread_id)
    existing = store.try_get(pk, "META")
    if existing is not None:
        return Context(kind=DIRECT, context_id=thread_id, thread_id=thread_id,
                       participants=frozenset(existing.get("agentIds")
                                              or {sender_id, recipient_id}))

    members = sorted({sender_id, recipient_id})
    names = [(store.try_get(K.agent_pk(a), "META") or {}).get("name") or a for a in members]
    stamp = now_iso()
    store.put({
        "pk": pk, "sk": "META", "entity": "Thread", "threadId": thread_id,
        "gsi1pk": "THREADS", "gsi1sk": stamp,
        # A room of two, so every reader of a room reads this one unchanged.
        # `direct` is what says it was opened by a Bot needing somewhere to put
        # one message, not by anyone asking for a room.
        "kind": "room", "direct": True,
        "title": " & ".join(names),
        "agentIds": members,
        "lastActivity": stamp,
        "createdBy": f"agent:{sender_id}",
        "status": "active",
    })
    return Context(kind=DIRECT, context_id=thread_id, thread_id=thread_id,
                   participants=frozenset(members))


def resolve_context(store: Store, *, task_id: str | None = None,
                    collaboration_context_id: str | None = None,
                    sender_id: str | None = None,
                    recipient_id: str | None = None) -> Context:
    """The one place a message's binding becomes a participant set.

    `task_id` names a Run: its owning agent plus every agent that has an
    *accepted* handoff on it. `collaboration_context_id` names a Thread:
    exactly its `agentIds`. Naming *neither* is a direct message, and opens the
    two-Bot context those two already implicitly share -- see `direct_context`.
    Naming both is still nothing: a message belongs to one conversation.
    """
    if task_id and collaboration_context_id:
        raise MessagingError(
            "message_agent takes task_id or collaboration_context_id, not both: "
            "a message belongs to one conversation")

    if not (task_id or collaboration_context_id):
        if not (sender_id and recipient_id):
            raise MessagingError(
                "message_agent needs a sender and a recipient, or a task_id or "
                "collaboration_context_id to bind the message to")
        return direct_context(store, sender_id=sender_id, recipient_id=recipient_id)

    if task_id:
        run = store.try_get(K.run_pk(task_id), "META")
        if run is None:
            raise MessagingError(f"no such task {task_id!r}")
        participants = {run["agentId"]}
        for h in store.query(run["pk"], sk_prefix="HOFF#", limit=200):
            if h.get("status") == "accepted":
                participants.add(h.get("fromAgentId"))
                participants.add(h.get("toAgentId"))
        participants.discard(None)
        return Context(kind="task", context_id=task_id, thread_id=run["threadId"],
                       participants=frozenset(participants))

    thread = store.try_get(K.thread_pk(collaboration_context_id), "META")
    if thread is None:
        raise MessagingError(f"no such collaboration context {collaboration_context_id!r}")
    return Context(kind="room", context_id=collaboration_context_id,
                  thread_id=collaboration_context_id,
                  participants=frozenset(thread.get("agentIds", [])))


def org_policy(store: Store) -> dict:
    """Org-level settings live on the user row -- there is one org per owner
    today (`identity.Principal.org_id` is the seam, not a promise)."""
    row = store.try_get(K.user_pk(store.owner_id), "META") or {}
    return row.get("policy") or {}


def allows_cross_context_escalation(store: Store) -> bool:
    return bool((org_policy(store).get("messaging") or {}).get("allowCrossContextEscalation"))


def limits_for_org(store: Store) -> MessagingLimits:
    cfg = org_policy(store).get("messaging") or {}
    return MessagingLimits(
        max_hop_depth=int(cfg.get("maxHopDepth", DEFAULT_MAX_HOP_DEPTH)),
        max_messages_per_task=int(cfg.get("maxMessagesPerTask", DEFAULT_MAX_MESSAGES_PER_TASK)),
        max_priority_wakes_per_window=int(
            cfg.get("maxPriorityWakesPerWindow", DEFAULT_MAX_PRIORITY_WAKES_PER_WINDOW)),
        priority_window_minutes=int(
            cfg.get("priorityWindowMinutes", DEFAULT_PRIORITY_WINDOW_MINUTES)),
        max_concurrent_runs_per_agent=int(
            cfg.get("maxConcurrentRunsPerAgent", DEFAULT_MAX_CONCURRENT_RUNS_PER_AGENT)),
        max_direct_messages_per_window=int(
            cfg.get("maxDirectMessagesPerWindow", DEFAULT_MAX_DIRECT_MESSAGES_PER_WINDOW)),
        direct_window_minutes=int(
            cfg.get("directWindowMinutes", DEFAULT_DIRECT_WINDOW_MINUTES)),
    )


def authorize(store: Store, *, sender_id: str, recipient_id: str, context: Context) -> PolicyResult:
    if sender_id in context.participants and recipient_id in context.participants:
        return PolicyResult(True, "both participants in context")
    if allows_cross_context_escalation(store):
        return PolicyResult(True, "cross-context escalation allowed by org policy", escalated=True)
    return PolicyResult(False, "sender or recipient is not a participant in this context")


def _messages_for_context(store: Store, context: Context, *, limit: int) -> list[dict]:
    """The context's most recent messages, and only as many as a ceiling reads.

    Newest first, bounded by the largest ceiling that looks at them. Reading a
    fixed thousand rows on every single send -- allowed or denied -- was latency
    spent on rows no check could reach, and reading the *oldest* thousand meant a
    long conversation counted hops for traces that had long since ended.
    """
    return store.query(_context_pk(context), sk_prefix="MSG#",
                       limit=max(1, limit), ascending=False)


def _within_window(stamp: str, minutes: int, *, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return (now - dt) <= timedelta(minutes=minutes)


def _log_denied(store: Store, context: Context, *, sender_id: str, recipient_id: str,
                reason: str, policy_result: PolicyResult) -> None:
    stamp = now_iso()
    store.put({
        "pk": _context_pk(context), "sk": f"MSGDENY#{stamp}#{ordered_suffix()}",
        "entity": "AgentMessageDenied",
        "gsi1pk": "MESSAGES", "gsi1sk": f"{stamp}#denied",
        "senderAgentId": sender_id, "recipientAgentId": recipient_id,
        "taskId": context.context_id if context.kind == "task" else None,
        "collaborationContextId": context.context_id if context.kind != "task" else None,
        "contextKind": context.kind,
        "reason": reason, "policyResult": policy_result.to_item(),
        "at": stamp,
    })


def send(store: Store, *, sender_agent_id: str, recipient_agent_id: str, args: dict) -> dict:
    """Validate, authorize, rate-limit and persist one agent-to-agent message.

    Raises `MessagingError` for anything that must never happen: no bound
    context, an unauthorized cross-context send, a hop depth or per-task
    message ceiling breach. Everything else about `priority` -- the wake
    rate window, concurrency, budget -- degrades the send to deferred rather
    than raising, because a message that already cleared authorization
    should not vanish just because it arrived at a busy moment.
    """
    text = (args.get("text") or "").strip()
    priority_requested = bool(args.get("priority"))
    task_id = args.get("task_id")
    collaboration_context_id = args.get("collaboration_context_id")

    context = resolve_context(store, task_id=task_id,
                              collaboration_context_id=collaboration_context_id,
                              sender_id=sender_agent_id,
                              recipient_id=recipient_agent_id)
    policy_result = authorize(store, sender_id=sender_agent_id,
                              recipient_id=recipient_agent_id, context=context)

    if not policy_result.allowed:
        _log_denied(store, context, sender_id=sender_agent_id, recipient_id=recipient_agent_id,
                   reason="not a participant in this context", policy_result=policy_result)
        raise MessagingError(policy_result.reason)

    limits = limits_for_org(store)
    direct = context.kind == DIRECT
    ceiling = (limits.max_direct_messages_per_window if direct
               else limits.max_messages_per_task)
    existing = _messages_for_context(
        store, context, limit=max(ceiling, limits.max_hop_depth))

    trace_id = args.get("trace_id") or new_id("trace_")
    hop_count = sum(1 for r in existing if r.get("traceId") == trace_id)
    if hop_count >= limits.max_hop_depth:
        _log_denied(store, context, sender_id=sender_agent_id, recipient_id=recipient_agent_id,
                   reason="max hop depth exceeded", policy_result=policy_result)
        raise MessagingError(
            f"hop depth {hop_count} at or beyond the max of {limits.max_hop_depth} "
            f"for trace {trace_id!r} -- this looks like a loop")

    if direct:
        # A task ends; a direct conversation does not, so the same ceiling read
        # as a lifetime total would silence two Bots permanently on a number
        # neither of them chose. It is a runaway guard, so it guards a window.
        recent = sum(1 for r in existing if r.get("at")
                     and _within_window(r["at"], limits.direct_window_minutes))
        if recent >= ceiling:
            _log_denied(store, context, sender_id=sender_agent_id,
                        recipient_id=recipient_agent_id,
                        reason="direct message ceiling exceeded", policy_result=policy_result)
            raise MessagingError(
                f"you and {recipient_agent_id!r} have exchanged {recent} direct messages "
                f"in the last {limits.direct_window_minutes} minutes, which is the most "
                f"allowed ({ceiling}); for work this involved, open a group chat or "
                "bring the operator in")
    elif len(existing) >= ceiling:
        _log_denied(store, context, sender_id=sender_agent_id, recipient_id=recipient_agent_id,
                   reason="message ceiling exceeded", policy_result=policy_result)
        raise MessagingError(
            f"{context.kind} {context.context_id!r} has reached its message ceiling "
            f"({ceiling})")

    recent_priority_wakes = sum(
        1 for r in existing
        if r.get("priorityGranted") and r.get("at") and _within_window(r["at"], limits.priority_window_minutes))
    priority_granted = priority_requested and recent_priority_wakes < limits.max_priority_wakes_per_window

    stamp = now_iso()
    message_id = new_id("msg_")
    row = {
        "pk": _context_pk(context), "sk": K.message_sk(stamp, ordered_suffix()),
        "entity": "AgentMessage", "messageId": message_id,
        "gsi1pk": "MESSAGES", "gsi1sk": f"{stamp}#{message_id}",
        "senderAgentId": sender_agent_id, "recipientAgentId": recipient_agent_id,
        "taskId": context.context_id if context.kind == "task" else None,
        "collaborationContextId": context.context_id if context.kind != "task" else None,
        "contextKind": context.kind,
        "parentMessageId": args.get("parent_message_id"),
        "parentHandoffId": args.get("parent_handoff_id"),
        "hopCount": hop_count, "traceId": trace_id,
        "policyResult": policy_result.to_item(),
        "priorityRequested": priority_requested, "priorityGranted": priority_granted,
        # `role`/`author`/`text` are what build_messages() and the console
        # already read for any Message row.
        "role": "assistant", "author": sender_agent_id, "text": text,
        "at": stamp,
    }
    store.put(row)
    return {"message": row, "context": context, "priority_granted": priority_granted}


def active_run_count_for_agent(store: Store, agent_id: str) -> int:
    """In-flight (non-terminal) runs for one agent, across every trigger.

    Scanned rather than indexed by agentId: at this scale a full RUNS listing
    read is cheap, and a dedicated GSI would only earn its keep once an
    agent's run volume actually made this slow.
    """
    rows = store.query_index("gsi1", "gsi1pk", "RUNS", limit=1000)
    return sum(1 for r in rows if r.get("agentId") == agent_id and r.get("state") not in _TERMINAL_VALUES)


def may_wake_now(store: Store, recipient_agent: dict, limits: MessagingLimits) -> tuple[bool, str]:
    """The concurrency + budget gate a priority wake must still pass.

    This runs in addition to the rate-window check already folded into
    `send()`'s `priority_granted`; failing here demotes an already-granted
    priority wake to "message delivered, recipient's run deferred" -- it
    never blocks or rewrites the message itself. Concurrency uses the
    recipient's own `budget.maxConcurrentRuns` (validated at agent-create
    time in `agents.validate_limits`) rather than a second, looser ceiling,
    so a priority wake is held to exactly the same concurrency limit a
    normal trigger already is.
    """
    from amazai import cost as C

    max_concurrent = int((recipient_agent.get("budget") or {}).get(
        "maxConcurrentRuns", limits.max_concurrent_runs_per_agent))
    active = active_run_count_for_agent(store, recipient_agent["agentId"])
    if active >= max_concurrent:
        return False, f"recipient at its concurrency ceiling ({max_concurrent})"

    budget = C.budget_for_agent(recipient_agent)
    spent_month = C.spent_this_month(store, recipient_agent["agentId"])
    verdict = C.check(budget, spent_this_run=0.0, spent_this_month=spent_month)
    if verdict.should_stop:
        return False, f"recipient over budget: {verdict.reason}"

    return True, "ok"
