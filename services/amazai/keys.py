"""DynamoDB key construction and identifier derivation.

Single source of truth for the key shapes in
`docs/architecture/03-data-model.md`. Handlers never build a pk/sk by hand.
"""

from __future__ import annotations

import hashlib
import re

# AgentCore rejects a runtimeSessionId shorter than this. Getting it wrong
# produces a validation error at invoke time, which is gotcha #2 in BUILD_PLAN.
MIN_SESSION_ID_LEN = 33
MAX_SESSION_ID_LEN = 100

_SAFE = re.compile(r"[^A-Za-z0-9_-]")


def _clean(value: str) -> str:
    return _SAFE.sub("-", value)


# --- entity keys ------------------------------------------------------------

def user_pk(subject: str) -> str:
    """Keyed on the Auth0 subject, which is the only identifier that cannot
    be re-registered by someone else."""
    return f"USER#{subject}"


def agent_pk(agent_id: str) -> str:
    return f"AGENT#{agent_id}"


def memory_sk(mem_id: str) -> str:
    return f"MEM#{mem_id}"


def grant_sk(connector_id: str) -> str:
    return f"GRANT#{connector_id}"


def connector_pk(owner_id: str, connector_id: str) -> str:
    """Owner-scoped, unlike most `_pk` helpers here.

    Every other entity's id (`run_`, `agent_`, ...) is `new_id()`-generated,
    so it is already globally unique and `ownerId` filtering on read is
    enough to keep two owners apart. A connector's id is not: it is derived
    from the app slug (`composio:slack`), the same value for every owner who
    connects Slack. Without `owner_id` in the key, a second owner installing
    the same app overwrites the first owner's row outright -- same pk/sk, a
    `put_item` with no uniqueness check -- silently dropping their grants and
    their Composio account reference. `ownerId` on the row is still what an
    authorization check reads; this is what stops the collision from
    happening before that check ever runs.
    """
    return f"CONNECTOR#{owner_id}#{connector_id}"


def thread_pk(thread_id: str) -> str:
    return f"THREAD#{thread_id}"


def message_sk(iso: str, rand: str) -> str:
    return f"MSG#{iso}#{rand}"


def message_sk_since(iso: str) -> tuple[str, str]:
    """An inclusive sort-key range covering every message at or after `iso`.

    The stamp is already in the sort key, so "the last hour of this thread" is a
    range read rather than a page of rows that has to be sized and then filtered.
    That distinction is load-bearing where a count decides something: see
    `collab._messages_for_context`.

    The upper bound is a character above anything `message_sk` can produce
    (timestamps and `ordered_suffix` are ASCII), which keeps neighbouring
    prefixes out -- `MSGDENY#` sorts *after* `MSG#`, so an open-ended `>=` read
    would collect the denial trail as well.
    """
    return f"MSG#{iso}", "MSG#\uffff"


def run_pk(run_id: str) -> str:
    return f"RUN#{run_id}"


def run_event_sk(seq: int) -> str:
    # Zero-padded so lexicographic order matches numeric order; without this,
    # EVT#10 sorts before EVT#9 and the timeline replays out of order.
    return f"EVT#{seq:012d}"


def approval_sk(approval_id: str) -> str:
    return f"APV#{approval_id}"


def paused_turn_sk(approval_id: str) -> str:
    """The shape of the model turn that stopped on this approval.

    Keyed by the approval rather than the run: one run can pause more than
    once, and each pause has to resume the turn *it* interrupted.
    """
    return f"PAUSE#{approval_id}"


def handoff_sk(handoff_id: str) -> str:
    return f"HOFF#{handoff_id}"


def usage_pk(year_month: str) -> str:
    """One partition per month.

    Deliberately not per-agent: the question the budget gate asks most often
    is "what has this workspace spent this month", and a per-agent partition
    would make that a fan-out over every agent on every model call.
    """
    return f"USAGE#{year_month}"


def cost_pk(agent_id: str, year_month: str) -> str:
    return f"COST#{agent_id}#{year_month}"


def routine_pk(routine_id: str) -> str:
    return f"ROUTINE#{routine_id}"


def settings_pk(owner_id: str) -> str:
    """One row per owner. Preferences are not per-agent: a notification
    someone does not want is not wanted from any companion."""
    return f"SETTINGS#{owner_id}"


def channel_pk(channel_type: str, external_id: str) -> str:
    return f"CHANNEL#{channel_type}#{external_id}"


def skill_pk(skill_id: str) -> str:
    return f"SKILL#{skill_id}"


def skill_version_sk(version: int) -> str:
    # Zero-padded for the same reason as run_event_sk: lexicographic order
    # must match numeric order so the latest version is a stable range query.
    return f"V#{version:06d}"


def task_pk(task_id: str) -> str:
    """Task-scoped memory's own partition, independent of the RUN# row.

    A message-spawned run for a recipient agent carries its `taskId` in
    `trigger`, not a fresh runId, so this key has to be addressable by that
    logical task id rather than by any one run's pk -- see
    docs/architecture/17-message-and-memory-authorization.md §2.
    """
    return f"TASK#{task_id}"


def task_child_sk(run_id: str) -> str:
    """A run spawned from an accepted handoff, tracked under its task's own
    partition. `task_pk` already holds that task's `MEM#` rows; a `CHILD#`
    row's own conditional status transition (`active` -> an outcome) is what
    makes a child's completion wake its coordinator exactly once -- see
    `amazai.handoffs.notify_coordinator_if_child`."""
    return f"CHILD#{run_id}"


def connection_pk(connection_id: str) -> str:
    return f"CONN#{connection_id}"


def idempotency_pk(key: str) -> str:
    return f"IDEM#{key}"


def runtime_sk(kind: str = "standard") -> str:
    """One account-level execution assignment under `USER#<owner>`.

    Kept under the owner partition because a standard harness belongs to the
    workspace, not to any logical Bot that happens to use it first.
    """
    return f"RUNTIME#{kind}"


# --- GSI keys ---------------------------------------------------------------

def run_state_gsi(state: str) -> str:
    """gsi2pk for the sweeper's per-state scan."""
    return f"RUNSTATE#{state}"


def approval_expiry_gsi() -> str:
    return "APVEXPIRY"


# --- derived identifiers ----------------------------------------------------

def session_id(thread_id: str) -> str:
    """Legacy v1 session key, derived from a thread only.

    Retained for runs that were already active or paused when account-level
    runtimes shipped. Those runs must resume on the exact old
    `(harnessArn, runtimeSessionId)` pair; changing either loses their
    continuation and session files. New runs use `bot_session_id` below.
    """
    base = f"amazai-{_clean(thread_id)}"
    if len(base) >= MIN_SESSION_ID_LEN:
        return base[:MAX_SESSION_ID_LEN]
    # Pad with a digest of the thread ID rather than a constant, so two short
    # thread IDs cannot collide onto one session.
    digest = hashlib.sha256(thread_id.encode()).hexdigest()
    return (base + "-" + digest)[:max(MIN_SESSION_ID_LEN, len(base) + 1)]


def bot_session_id(owner_id: str, agent_id: str, thread_id: str, *, epoch: int = 0) -> str:
    """A stable v2 AgentCore session for one logical Bot in one thread.

    A shared harness makes the complete namespace `(owner, Bot, thread)`.
    `threadId` alone is unsafe: every Bot in a room has the same one, so room
    kickoff would send several model/tool invocations into one microVM.

    The readable prefix helps operations; the digest carries the untruncated
    triple (including the owner) without exposing the Auth0 subject. Kept under
    AgentCore's 100-character maximum and always above its 33-character floor.

    `epoch` rotates this triple onto an unrelated session without changing
    anything else about it. It is left out of the hash entirely at its default
    of 0, so a pair nobody has ever had to rotate gets exactly the id it always
    got. `runs.mark_session_dirty` is the only writer of a nonzero one --
    a turn that ends with a tool call's result computed but never sent back
    (a round or budget ceiling, a stream error, a cancellation) leaves the
    AgentCore session expecting an answer it will never get, and every
    invocation after that on the same session fails with `Inline function
    result is missing toolUseId` whether or not it did anything wrong itself.
    Rotating the pair is what lets the *next* run start clean rather than
    inheriting a session that is already broken.
    """
    seed = f"{owner_id}\0{agent_id}\0{thread_id}"
    if epoch:
        seed += f"\0{epoch}"
    digest = hashlib.sha256(seed.encode()).hexdigest()[:20]
    agent = _clean(agent_id)[:24].strip("-_") or "bot"
    thread = _clean(thread_id)[:36].strip("-_") or "thread"
    value = f"amazai-v2-{agent}-{thread}-{digest}"
    return value[:MAX_SESSION_ID_LEN]


def is_bot_session_id(value: str) -> bool:
    return bool(value and value.startswith("amazai-v2-"))


def session_epoch_sk(thread_id: str) -> str:
    """Where one (agent, thread) pair's session epoch is kept.

    Lives under the *agent's* partition, alongside its own META row: the pair
    this counts already has a home there, and a distinct sort key is a
    distinct item -- no new partition has to be reasoned about for it.
    """
    return f"SESSEPOCH#{thread_id}"


def schedule_idempotency_key(routine_id: str, scheduled_time: str) -> str:
    """EventBridge Scheduler is at-least-once; this makes the second fire a no-op."""
    return f"sched:{routine_id}:{scheduled_time}"


def webhook_idempotency_key(provider: str, delivery_id: str) -> str:
    return f"hook:{provider}:{delivery_id}"


def tool_idempotency_key(run_id: str, tool_use_id: str) -> str:
    """Passed to connectors so a resumed or retried run cannot double-create."""
    return f"{run_id}:{tool_use_id}"
