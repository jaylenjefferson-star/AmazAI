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


def connector_pk(connector_id: str) -> str:
    return f"CONNECTOR#{connector_id}"


def thread_pk(thread_id: str) -> str:
    return f"THREAD#{thread_id}"


def message_sk(iso: str, rand: str) -> str:
    return f"MSG#{iso}#{rand}"


def run_pk(run_id: str) -> str:
    return f"RUN#{run_id}"


def run_event_sk(seq: int) -> str:
    # Zero-padded so lexicographic order matches numeric order; without this,
    # EVT#10 sorts before EVT#9 and the timeline replays out of order.
    return f"EVT#{seq:012d}"


def approval_sk(approval_id: str) -> str:
    return f"APV#{approval_id}"


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


def connection_pk(connection_id: str) -> str:
    return f"CONN#{connection_id}"


def idempotency_pk(key: str) -> str:
    return f"IDEM#{key}"


# --- GSI keys ---------------------------------------------------------------

def run_state_gsi(state: str) -> str:
    """gsi2pk for the sweeper's per-state scan."""
    return f"RUNSTATE#{state}"


def approval_expiry_gsi() -> str:
    return "APVEXPIRY"


# --- derived identifiers ----------------------------------------------------

def session_id(thread_id: str) -> str:
    """Derive a stable AgentCore runtimeSessionId from a thread ID.

    Must be deterministic: resuming a paused run depends on landing on the same
    session, which is what keeps the agent's files and git state intact across
    an approval pause.
    """
    base = f"amazai-{_clean(thread_id)}"
    if len(base) >= MIN_SESSION_ID_LEN:
        return base
    # Pad with a digest of the thread ID rather than a constant, so two short
    # thread IDs cannot collide onto one session.
    digest = hashlib.sha256(thread_id.encode()).hexdigest()
    return (base + "-" + digest)[:max(MIN_SESSION_ID_LEN, len(base) + 1)]


def schedule_idempotency_key(routine_id: str, scheduled_time: str) -> str:
    """EventBridge Scheduler is at-least-once; this makes the second fire a no-op."""
    return f"sched:{routine_id}:{scheduled_time}"


def webhook_idempotency_key(provider: str, delivery_id: str) -> str:
    return f"hook:{provider}:{delivery_id}"


def tool_idempotency_key(run_id: str, tool_use_id: str) -> str:
    """Passed to connectors so a resumed or retried run cannot double-create."""
    return f"{run_id}:{tool_use_id}"
