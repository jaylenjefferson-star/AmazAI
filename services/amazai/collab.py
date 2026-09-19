"""Authorization boundary for agent-to-agent messaging.

Implements the stricter half of `docs/architecture/17-message-and-memory-
authorization.md` §1. A message is never open broadcast: it is bound to a
task (a Run) or a collaboration context (a Thread), and the sender and
recipient must both be participants in that context unless an org policy
explicitly allows escalation. `priority: true` only ever requests an
expedited wake -- it never buys its way past a hop-depth, rate, concurrency
or budget ceiling; see `send()` and `may_wake_now()`.

Every send, allowed or denied, leaves a row under the context's own
partition tagged `gsi1pk: MESSAGES`, so the full trail -- who asked to talk
to whom, in what task, and whether policy allowed it -- is one query away
regardless of whether the message was actually delivered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from amazai import keys as K
from amazai.states import TERMINAL
from amazai.store import Store, new_id, now_iso, ordered_suffix

#: Mirrors the handoff loop-prevention depth in
#: docs/architecture/09-multi-agent.md -- one shared notion of "too deep".
DEFAULT_MAX_HOP_DEPTH = 3
DEFAULT_MAX_MESSAGES_PER_TASK = 200
DEFAULT_MAX_PRIORITY_WAKES_PER_WINDOW = 5
DEFAULT_PRIORITY_WINDOW_MINUTES = 60
DEFAULT_MAX_CONCURRENT_RUNS_PER_AGENT = 3

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
    kind: str                    # "task" | "room"
    context_id: str              # a runId (task) or a threadId (room)
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


def _context_pk(context: Context) -> str:
    """Message history for both kinds of context lives in a Thread row: a
    `room` context's own thread, or the task's owning run's thread -- so a
    priority-woken recipient's run (spawned onto that same threadId) sees
    the conversation with no separate lookup, and the console's task-bound
    room is just this thread."""
    return K.thread_pk(context.thread_id)


def resolve_context(store: Store, *, task_id: str | None = None,
                    collaboration_context_id: str | None = None) -> Context:
    """The one place task_id/collaboration_context_id become a participant set.

    `task_id` names a Run: its owning agent plus every agent that has an
    *accepted* handoff on it. `collaboration_context_id` names a Thread:
    exactly its `agentIds`. Either is a valid binding target; neither nor
    both is not.
    """
    if bool(task_id) == bool(collaboration_context_id):
        raise MessagingError(
            "message_agent requires exactly one of task_id or collaboration_context_id")

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
    )


def authorize(store: Store, *, sender_id: str, recipient_id: str, context: Context) -> PolicyResult:
    if sender_id in context.participants and recipient_id in context.participants:
        return PolicyResult(True, "both participants in context")
    if allows_cross_context_escalation(store):
        return PolicyResult(True, "cross-context escalation allowed by org policy", escalated=True)
    return PolicyResult(False, "sender or recipient is not a participant in this context")


def _messages_for_context(store: Store, context: Context) -> list[dict]:
    return store.query(_context_pk(context), sk_prefix="MSG#", limit=1000)


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
        "collaborationContextId": context.context_id if context.kind == "room" else None,
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
                              collaboration_context_id=collaboration_context_id)
    policy_result = authorize(store, sender_id=sender_agent_id,
                              recipient_id=recipient_agent_id, context=context)

    if not policy_result.allowed:
        _log_denied(store, context, sender_id=sender_agent_id, recipient_id=recipient_agent_id,
                   reason="not a participant in this context", policy_result=policy_result)
        raise MessagingError(policy_result.reason)

    limits = limits_for_org(store)
    existing = _messages_for_context(store, context)

    trace_id = args.get("trace_id") or new_id("trace_")
    hop_count = sum(1 for r in existing if r.get("traceId") == trace_id)
    if hop_count >= limits.max_hop_depth:
        _log_denied(store, context, sender_id=sender_agent_id, recipient_id=recipient_agent_id,
                   reason="max hop depth exceeded", policy_result=policy_result)
        raise MessagingError(
            f"hop depth {hop_count} at or beyond the max of {limits.max_hop_depth} "
            f"for trace {trace_id!r} -- this looks like a loop")

    if len(existing) >= limits.max_messages_per_task:
        _log_denied(store, context, sender_id=sender_agent_id, recipient_id=recipient_agent_id,
                   reason="message ceiling exceeded", policy_result=policy_result)
        raise MessagingError(
            f"{context.kind} {context.context_id!r} has reached its message ceiling "
            f"({limits.max_messages_per_task})")

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
        "collaborationContextId": context.context_id if context.kind == "room" else None,
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
