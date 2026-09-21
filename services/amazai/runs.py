"""Run record lifecycle: create, advance, pause, seal.

Holds the persistence rule from docs/architecture/05-run-lifecycle.md:
nothing irreversible happens until the intent to do it is durable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from amazai import keys as K
from amazai.states import RunState, is_terminal, transition
from amazai.store import Store, new_id, now_iso

DEFAULT_DEADLINE_MINUTES = 15


def create(store: Store, *, agent_id: str, thread_id: str, goal: str,
           trigger: dict | None = None, deadline_minutes: int = DEFAULT_DEADLINE_MINUTES,
           run_id: str | None = None) -> dict:
    """`run_id` is for a caller that has already *claimed* an id (an idempotency
    key for a routine fire): the id it stored must be the id the run has, or a
    retried caller is handed the identifier of a run that never existed."""
    run_id = run_id or new_id("run_")
    deadline = datetime.now(timezone.utc) + timedelta(minutes=deadline_minutes)

    item = {
        "pk": K.run_pk(run_id), "sk": "META",
        "entity": "Run", "runId": run_id,
        "gsi1pk": "RUNS", "gsi1sk": now_iso(),
        "gsi2pk": K.run_state_gsi(RunState.QUEUED.value), "gsi2sk": now_iso(),
        "agentId": agent_id, "threadId": thread_id,
        # v2 includes the owner and logical Bot. On an account-level harness a
        # room's thread ID alone would put every parallel Bot in the same
        # microVM. Existing rows keep their v1 ID and are never rewritten.
        "sessionId": K.bot_session_id(store.owner_id, agent_id, thread_id),
        "sessionVersion": 2,
        # Set exactly once by standard_runtime.pin_run before the first model
        # call. Retry and approval resume use the pinned ARN even if deployment
        # policy or the Bot row changes while work is paused.
        "runtimeHarnessArn": None,
        "goal": goal,
        "trigger": trigger or {"type": "user"},
        "state": RunState.QUEUED.value,
        "plan": [], "toolPaths": [],
        "cursor": {"turn": 0, "lastEventSeq": 0},
        "pending": None,
        "attempt": 0, "toolErrorCount": 0, "toolCallCount": 0,
        "consecutiveToolErrors": 0,
        "heartbeatAt": now_iso(),
        "deadlineAt": deadline.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "costUsd": 0.0,
        "evidenceKey": None,
        "startedAt": now_iso(), "endedAt": None,
    }
    return store.put(item, unique=True)


def advance(store: Store, run: dict, to: RunState, **changes) -> dict:
    """Move a run to a new state, validating the transition first.

    The conditional write on the current state means two workers racing on the
    same run cannot both win; the loser re-reads.
    """
    frm = RunState(run["state"])
    transition(frm, to)

    changes = dict(changes)
    changes["state"] = to.value
    changes["gsi2pk"] = K.run_state_gsi(to.value)
    changes["gsi2sk"] = now_iso()
    changes["heartbeatAt"] = now_iso()
    if is_terminal(to) and not run.get("endedAt"):
        changes["endedAt"] = now_iso()

    return store.update(run["pk"], "META", changes, expect={"state": frm.value})


def heartbeat(store: Store, run: dict) -> dict:
    return store.update(run["pk"], "META",
                        {"heartbeatAt": now_iso(), "gsi2sk": now_iso()})


def record_event(store: Store, run: dict, seq: int, kind: str, **payload) -> dict:
    return store.put({
        "pk": run["pk"], "sk": K.run_event_sk(seq),
        "entity": "RunEvent", "seq": seq, "kind": kind,
        "at": now_iso(), **payload,
    })


def pause_for_approval(store: Store, run: dict, approval: dict) -> dict:
    """Park the run on a human decision.

    Nothing is held open while paused: no task token, no waiting execution, no
    billed compute. The entire resumable state is this row plus the session ID.
    """
    return advance(store, run, RunState.AWAITING_APPROVAL, pending={
        "kind": "approval",
        "approvalId": approval["approvalId"],
        "toolUseId": approval.get("toolUseId"),
    })


def is_cancelled(store: Store, run: dict) -> bool:
    """Re-read the cancel flag between stream events and tool calls."""
    fresh = store.try_get(run["pk"], "META")
    if not fresh:
        return False
    return fresh["state"] in (RunState.CANCELLING.value, RunState.CANCELLED.value)


def deadline_passed(run: dict, *, now: datetime | None = None) -> bool:
    deadline = run.get("deadlineAt")
    if not deadline:
        return False
    now = now or datetime.now(timezone.utc)
    dt = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
    return now >= dt


def heartbeat_stale(run: dict, *, minutes: int = 10, now: datetime | None = None) -> bool:
    hb = run.get("heartbeatAt")
    if not hb:
        return True
    now = now or datetime.now(timezone.utc)
    dt = datetime.fromisoformat(hb.replace("Z", "+00:00"))
    return (now - dt) > timedelta(minutes=minutes)
