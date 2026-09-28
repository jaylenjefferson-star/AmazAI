"""Ambient signals: what the org has been up to, read back from durable state.

Routines fire on a schedule and a live socket lights a Companion up while a
browser is open, but between those the console can read as inert even when the
org has just finished real work. This module answers "what has been happening
lately" the same way `presence.py` answers "what is happening right now": as a
pure read over rows the control plane already persists, never a live model and
never a process kept warm for an idle Companion.

A signal here is low-risk by construction. It reports only things that have
already durably happened -- a task closed, a coordinator still waiting on its
teammates, an artifact just produced -- so surfacing one can neither start work
nor spend money; it only describes committed state. The derivation is bounded
(a fixed newest-first window per kind) so a busy month cannot turn the feed into
noise, and it adds no GSI: it reuses the same gsi1 `TASKS` and `ARTIFACTS`
listings `presence.derive`, GET /tasks and GET /artifacts already read.

`assemble` is split from `derive` for the same reason it is in `presence.py`:
the whole fold is a pure function of already-queried rows, so it is testable
without a store and provably read-only.
"""

from __future__ import annotations

from amazai import artifacts as AR
from amazai.store import Store

#: How many signals of each kind the feed carries. A window, not a log: the
#: point is "recently", and an unbounded list would be noise, not a signal.
MAX_PER_KIND = 8

#: Terminal task statuses. A task leaves the "waiting on teammates" view the
#: moment it closes, whatever its outcome, so a done task never reads as still
#: coordinating.
_CLOSED_TASK_STATUSES = frozenset({"closed", "cancelled"})


def _completion_signal(task: dict) -> dict:
    outcome = task.get("outcome") or "done"
    return {
        "kind": "completed",
        "at": task.get("closedAt") or task.get("createdAt") or "",
        "agentId": task.get("coordinatorAgentId"),
        "taskId": task.get("taskId"),
        "threadId": task.get("threadId"),
        "outcome": outcome,
        "title": (task.get("goal") or "").strip()[:200],
    }


def _waiting_signal(task: dict, pending: int) -> dict:
    return {
        "kind": "waiting",
        "at": task.get("createdAt") or "",
        "agentId": task.get("coordinatorAgentId"),
        "taskId": task.get("taskId"),
        "threadId": task.get("threadId"),
        "pendingChildren": pending,
        "title": (task.get("goal") or "").strip()[:200],
    }


def _artifact_signal(artifact: dict) -> dict:
    return {
        "kind": "produced",
        "at": artifact.get("createdAt") or "",
        "agentId": artifact.get("createdByAgentId"),
        "artifactId": artifact.get("artifactId"),
        "taskId": artifact.get("taskId"),
        "threadId": artifact.get("threadId"),
        "artifactType": artifact.get("artifactType") or "other",
        "title": (artifact.get("name") or "").strip()[:200],
    }


def _newest(signals: list[dict], limit: int) -> list[dict]:
    """Newest-first, capped. `at` is an ISO timestamp, so a string sort is a
    time sort; a signal missing one loses to any that has one."""
    return sorted(signals, key=lambda s: s.get("at") or "", reverse=True)[:limit]


def assemble(tasks: list[dict], artifacts: list[dict], *,
             limit: int = MAX_PER_KIND) -> dict:
    """Fold already-queried rows into the three ambient buckets.

    Pure: takes rows, returns a dict of newest-first, bounded signal lists,
    touching nothing. `completed` is the recently closed tasks, `waiting` is the
    open tasks whose coordinator still owes an answer while children are
    outstanding, and `produced` is the artifacts just written. Each bucket is
    the same durable state a person could reconstruct by hand from the rows;
    none of it is derived from a live model or a warm process.
    """
    completed: list[dict] = []
    waiting: list[dict] = []
    for task in tasks:
        status = task.get("status")
        if status in _CLOSED_TASK_STATUSES:
            completed.append(_completion_signal(task))
            continue
        pending = int(task.get("pendingChildren") or 0)
        if pending > 0 and task.get("coordinatorAgentId"):
            waiting.append(_waiting_signal(task, pending))

    produced = [_artifact_signal(a) for a in artifacts
                if a.get("status") == "ready"]

    return {
        "completed": _newest(completed, limit),
        "waiting": _newest(waiting, limit),
        "produced": _newest(produced, limit),
    }


def derive(store: Store, *, limit: int = MAX_PER_KIND) -> dict:
    """The read behind GET /ambient: the org's recent activity from durable rows.

    Reuses the gsi1 `TASKS` listing (`presence.derive`, GET /tasks) and
    `artifacts.list_for_owner` (GET /artifacts) rather than adding a GSI. Purely
    a read -- no writes, no model calls -- so it is safe to poll alongside
    /presence, and the fold lives in `assemble` so nothing on this path can
    write.
    """
    tasks = store.query_index("gsi1", "gsi1pk", "TASKS", limit=200)
    artifacts = AR.list_for_owner(store, status="ready", limit=limit * 4)
    return assemble(tasks, artifacts, limit=limit)
