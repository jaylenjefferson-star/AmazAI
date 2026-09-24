"""Handoff acceptance: the missing other half of `_record_handoff`.

Until now a handoff was write-only -- `orchestrator._record_handoff` put a
`status: "proposed"` row and nothing ever read it back. No code accepted one,
woke the receiver, or checked the receiver's own grant/budget/policy first.
This module is that other half, shared by two callers that must produce
identically-shaped state: the operator deciding by hand
(`POST /handoffs/{runId}/{hoffId}`) and the system deciding automatically
(`orchestrator._record_handoff`, right after a handoff is proposed).

Auto-accept is deliberately conservative: anything this cannot resolve
cleanly -- an action on the always-approve floor, a receiver missing a named
app's grant, a hop-depth or task-volume ceiling, a concurrency/budget
ceiling -- is a reason to leave the handoff `proposed` for a human, never a
reason to wave it through. The one floor `policy.evaluate` enforces
everywhere else (`ALWAYS_APPROVE`/`NEVER_APPROVABLE`) is never bypassed here
just because the receiver's grant would otherwise cover the call.
"""

from __future__ import annotations

from amazai import agents as A, artifacts, collab, connectors, keys as K, metrics, policy, runs
from amazai.store import Conflict, Store, now_iso

#: Outstanding children one task may have at once. A task-level analogue of
#: `collab.MAX_ROOM_MEMBERS`: past this, a fan-out stops being a bounded burst
#: of parallel work and starts being a way to spend an unbounded amount of
#: compute from one operator message. Checked against the task's live
#: `pendingChildren` counter, not a count of `CHILD#` rows -- see `accept`.
MAX_ACTIVE_CHILDREN_PER_TASK = 6

#: Non-terminal runs one thread (room or DM) may have outstanding at once,
#: across every task in it. Wider than `MAX_ACTIVE_CHILDREN_PER_TASK`
#: deliberately: a task's own counter resets once it settles, so a
#: coordinator that keeps starting fresh tasks in the same room is not
#: bounded by that alone -- this is the ceiling that actually stops a room
#: from spending unbounded concurrent compute over time.
MAX_ACTIVE_RUNS_PER_ROOM = 12


class HandoffError(ValueError):
    """A handoff cannot be decided as asked -- no such recipient, or it was
    already decided. Distinct from `collab.MessagingError`, which is a policy
    refusal (hop depth, ceiling, not a participant) rather than a bad request."""


def _receiver(store: Store, to_agent_id: str) -> dict:
    receiver = store.try_get(K.agent_pk(store.owner_id, to_agent_id), "META")
    if receiver is None or receiver.get("status") not in A.RUNNABLE:
        raise HandoffError(f"no such active recipient {to_agent_id!r}")
    return receiver


def _task_id_for(coordinator_run: dict) -> str:
    """The durable task a run's handoffs belong to.

    The same identity `_drive` already uses for task-scoped memory: a run
    started directly by the operator is its own task; a run spawned to
    continue one carries the original `taskId` forward in its trigger. One
    concept, not two, so a handoff proposed by a continuation run still lands
    on the task the operator actually started.
    """
    return (coordinator_run.get("trigger") or {}).get("taskId") or coordinator_run["runId"]


def can_auto_accept(store: Store, handoff: dict, receiver: dict, coordinator_run: dict, *,
                    limits: collab.MessagingLimits | None = None) -> tuple[bool, str]:
    """Whether `handoff` may skip the human and wake `receiver` now.

    Five gates, in order of how cheaply they refuse:

    1. The requested action, if named, is never on the always-approve floor
       or the never-approvable list -- checked with the exact same
       `policy.evaluate` patterns every other tool call is held to, so a
       handoff is not a second, weaker way to reach the same action.
    2. If the action names an app (`"slack.post"` -> `slack`), the receiver
       actually holds a grant for it. A freeform action ("review the draft")
       names no app and needs none.
    3. The task this handoff belongs to is under its own fan-out ceiling --
       `MAX_ACTIVE_CHILDREN_PER_TASK` outstanding children at once, so one
       operator message cannot spend unbounded concurrent compute.
    4. The room this task lives in is under its own ceiling --
       `MAX_ACTIVE_RUNS_PER_ROOM` runs in flight across every task the room
       has, not just this one, so a coordinator cannot get around gate 3 by
       simply starting a fresh task each time the last one settles.
    5. The receiver is under its own concurrency and budget ceiling -- the
       same `collab.may_wake_now` gate a priority `message_agent` wake
       already has to clear.

    Hop depth and the task's message ceiling are *not* checked here: they are
    enforced by `collab.send` inside `accept()`, because they require a
    reservation (the message row itself) to be race-free, and a plain
    boolean check here could pass and then lose to a concurrent accept.
    """
    action = (handoff.get("requestedAction") or "").strip()
    if action:
        never = policy.matching_pattern(action, policy.NEVER_APPROVABLE)
        if never:
            return False, f"{action!r} is never approvable ({never})"
        floor = policy.matching_pattern(action, policy.ALWAYS_APPROVE)
        if floor:
            return False, f"{action!r} is on the always-approve floor ({floor})"
        app_slug = action.split(".", 1)[0] if "." in action else ""
        if app_slug:
            granted = connectors.granted_apps(store, receiver["agentId"])
            if connectors.grant_for(granted, app_slug) is None:
                name = receiver.get("name", receiver["agentId"])
                return False, f"{name} has no grant for {app_slug!r}"

    task = store.try_get(K.task_pk(_task_id_for(coordinator_run)), "META")
    pending = int((task or {}).get("pendingChildren") or 0)
    if pending >= MAX_ACTIVE_CHILDREN_PER_TASK:
        return False, (f"this task already has {pending} handoffs outstanding "
                       f"(max {MAX_ACTIVE_CHILDREN_PER_TASK} at once)")

    room_active = collab.active_run_count_for_thread(store, coordinator_run["threadId"])
    if room_active >= MAX_ACTIVE_RUNS_PER_ROOM:
        return False, (f"this room already has {room_active} runs in flight "
                       f"(max {MAX_ACTIVE_RUNS_PER_ROOM} at once)")

    limits = limits or collab.limits_for_org(store)
    allowed, reason = collab.may_wake_now(store, receiver, limits)
    if not allowed:
        return False, reason

    return True, "ok"


def ensure_task(store: Store, task_id: str, coordinator_run: dict) -> dict:
    """The durable task record, created the first time this task fans out.

    Idempotent by construction (`unique=True` on the first writer, a plain
    read on every loser) rather than a get-then-put -- two handoffs accepted
    in the same task at nearly the same moment must not race to create two
    task rows for one task.
    """
    created_at = now_iso()
    item = {
        "pk": K.task_pk(task_id), "sk": "META",
        "entity": "Task", "taskId": task_id,
        "createdAt": created_at,
        # Listable by status without a full-table scan -- "every open task"
        # is otherwise unanswerable, since a Task lives only under its own
        # pk. gsi1sk is rewritten on every status move (see `_set_status`)
        # so the listing stays a prefix range on the *current* status.
        "gsi1pk": "TASKS", "gsi1sk": K.tasks_gsi1_sk("open", created_at),
        "coordinatorAgentId": coordinator_run["agentId"],
        "coordinatorRunId": coordinator_run["runId"],
        "threadId": coordinator_run["threadId"],
        "goal": coordinator_run.get("goal", ""),
        "status": "open",
        # A fan-in reference count, not a log: incremented once per accepted
        # handoff (`accept`), decremented once per settled child
        # (`notify_coordinator_if_child`). The settle that brings it to zero
        # is, unambiguously, the last one -- see `Store.increment`.
        "pendingChildren": 0,
    }
    try:
        return store.put(item, unique=True)
    except Conflict:
        return store.get(K.task_pk(task_id), "META")


def _artifact_refs_note(store: Store, artifact_ids: list[str]) -> str:
    """Names and ids only, appended to a handoff's brief -- never content.
    An id that does not resolve (wrong tenant, already deleted, a typo the
    model made up) is silently left out rather than failing the whole
    handoff over a reference that was never load-bearing to begin with."""
    lines = []
    for aid in artifact_ids:
        row = artifacts.try_get(store, aid)
        if row and row.get("status") != "deleted":
            lines.append(f"- {row['name']} (artifactId: {aid}, type: {row.get('artifactType', 'other')})")
    if not lines:
        return ""
    return ("\n\nReferenced artifacts (call read_artifact for any you need):\n"
           + "\n".join(lines))


def accept(store: Store, coordinator_run: dict, handoff: dict, *,
          decided_by: str) -> dict:
    """Accept a proposed handoff: claim it, bind it to the task, spawn the
    receiver's run.

    Raises `Conflict` if the handoff was already decided (by either path --
    a human clicking accept and an auto-accept racing each other resolve the
    same way an approval decided twice does: the loser's write fails, and it
    is the caller's job to re-read and explain that, not this function's).
    Raises `HandoffError` for a bad recipient, `collab.MessagingError` for a
    hop-depth/ceiling/authorization refusal.

    The handoff row's own conditional transition is claimed *first*, before
    anything else happens -- so a caller that loses the race never spawns a
    second run or double-counts a task's child.
    """
    receiver = _receiver(store, handoff["toAgentId"])
    task_id = _task_id_for(coordinator_run)

    # `K.run_pk(task_id)`, not `coordinator_run["pk"]`: a handoff proposed by
    # a continuation run still lives under the task's root run (see
    # `orchestrator._record_handoff`), and this has to agree with wherever it
    # was actually written or the conditional update below finds nothing.
    decided = store.update(K.run_pk(task_id), K.handoff_sk(handoff["handoffId"]), {
        "status": "accepted", "decidedBy": decided_by, "decidedAt": now_iso(),
        "gsi1sk": f"accepted#{now_iso()}",
    }, expect={"status": "proposed"})

    ensure_task(store, task_id, coordinator_run)
    trace_id = (coordinator_run.get("trigger") or {}).get("traceId")

    # The *delivery* still lands in the task's own (the coordinator's) thread --
    # collab.send's task-context resolution -- so the operator and anyone else
    # in that conversation sees "handed off to Finance" exactly as before.
    brief = decided.get("goal") or decided.get("requestedAction") or "a teammate handed this off to you"
    brief += _artifact_refs_note(store, decided.get("artifactRefs") or [])
    outcome = collab.send(store, sender_agent_id=decided["fromAgentId"],
                          recipient_agent_id=decided["toAgentId"],
                          args={"text": brief,
                                "task_id": task_id,
                                **({"trace_id": trace_id} if trace_id else {}),
                                "parent_handoff_id": decided["handoffId"]})
    message = outcome["message"]

    # The *execution* does not: a child run pinned to the coordinator's own
    # thread would (a) load the coordinator's last MAX_HISTORY messages as its
    # own context -- everything the operator ever said to the coordinator, not
    # a bounded brief -- and (b) collide sessions with any other child this
    # same receiving agent is concurrently running, since AgentCore's session
    # id is keyed on (owner, agentId, threadId) alone. A thread dedicated to
    # this one handoff gives the child its own session and starts it with
    # nothing but the brief `runs.create` sends as its opening turn.
    child_thread_id = K.handoff_child_thread_id(decided["handoffId"])
    try:
        store.put({
            "pk": K.thread_pk(store.owner_id, child_thread_id), "sk": "META",
            "entity": "Thread", "threadId": child_thread_id,
            # Deliberately no gsi1pk/gsi1sk: this is not a conversation the
            # operator opens from the inbox, only the execution context for
            # one handoff -- reachable from the TaskChild row's own threadId.
            "kind": "task_child", "title": f"{decided['toAgentId']}: {decided.get('goal', '')[:80]}",
            "agentIds": [decided["toAgentId"]],
            "lastActivity": now_iso(), "createdBy": f"agent:{decided['fromAgentId']}",
            "taskId": task_id, "handoffId": decided["handoffId"], "status": "active",
        }, unique=True)
    except Conflict:
        pass  # a crashed-and-retried accept() of this same handoff already made it

    child = runs.create(
        store, agent_id=decided["toAgentId"], thread_id=child_thread_id,
        goal=message["text"],
        trigger={"type": "handoff", "handoffId": decided["handoffId"],
                 "coordinatorRunId": coordinator_run["runId"], "taskId": task_id,
                 "fromAgentId": decided["fromAgentId"], "traceId": message["traceId"]})

    store.put({
        "pk": K.task_pk(task_id), "sk": K.task_child_sk(child["runId"]),
        "entity": "TaskChild", "taskId": task_id, "runId": child["runId"],
        "agentId": child["agentId"], "handoffId": decided["handoffId"],
        "coordinatorRunId": coordinator_run["runId"], "threadId": child_thread_id,
        "status": "active",
    })
    # After the child row exists, never before: `notify_coordinator_if_child`
    # queries `CHILD#` rows once this counter reaches zero, and that query
    # must never be able to find fewer rows than the count promised.
    active_now = store.increment(K.task_pk(task_id), "META", "pendingChildren", 1)
    metrics.emit("TaskActiveChildren", active_now,
                 taskId=task_id, childRunId=child["runId"],
                 coordinatorRunId=coordinator_run["runId"], agentId=child["agentId"])

    return {"handoff": decided, "child": child, "receiver": receiver}


def reject(store: Store, coordinator_run: dict, handoff: dict, *,
          decided_by: str, note: str = "") -> dict:
    task_id = _task_id_for(coordinator_run)
    return store.update(K.run_pk(task_id), K.handoff_sk(handoff["handoffId"]), {
        "status": "rejected", "decidedBy": decided_by, "decidedAt": now_iso(),
        "note": note, "gsi1sk": f"rejected#{now_iso()}",
    }, expect={"status": "proposed"})


#: A child run's terminal `RunState.value` -> the outcome recorded on its
#: `TaskChild` row and reported to the coordinator. Deliberately coarser than
#: `RunState`: the coordinator needs to know whether to treat this as work
#: done, work that needs redoing, or work abandoned -- not the exact state
#: name, which `EvidenceWriter` already preserves for anyone who needs it.
_CHILD_OUTCOME = {
    "COMPLETED": "done", "PARTIAL": "done",
    "FAILED": "failed", "EXPIRED": "failed",
    "CANCELLED": "cancelled",
}


def _digest_line(store: Store, child_row: dict) -> str:
    name = (store.try_get(K.agent_pk(store.owner_id, child_row["agentId"]), "META") or {}).get(
        "name", child_row["agentId"])
    status = child_row.get("status", "active")
    summary = (child_row.get("summary") or "").strip()
    line = f"- {name}: {status}" + (f" -- {summary[:300]}" if summary else "")
    # Names and ids, never content -- the coordinator retrieves one with
    # read_artifact only if it actually needs what is inside it.
    produced = artifacts.list_for_run(store, child_row["runId"], limit=10)
    if produced:
        refs = ", ".join(f"{a['name']} (artifactId: {a['artifactId']})" for a in produced)
        line += f"\n  artifacts: {refs}"
    return line


def notify_coordinator_if_child(store: Store, run: dict, state_value: str,
                                summary: str) -> dict | None:
    """A run that just settled reports to its coordinator, if it was spawned
    from an accepted handoff -- but only *wakes* it once every child from the
    same fan-out has reported in.

    Two separate guarantees, doing two different jobs:

    1. **This run is reported at most once.** One conditional write: the
       `TaskChild` row's `status` moves `active` -> its outcome with
       `expect={"status": "active"}`. A second settle of the *same* run --
       `_fail` and `_finish` both reachable on one dirty exit, a sweeper
       sweep racing a live invocation's own settle -- loses that race and
       returns `None`.
    2. **The coordinator wakes at most once per completed batch**, not once
       per child. `Store.increment` decrements the task's `pendingChildren`
       and hands back the value *this call* left it at; only the call that
       lands it at zero or below is -- unambiguously, because the decrement
       is server-side and serialized -- the last sibling to report, and only
       that call spawns a continuation. Two children finishing a moment apart
       each still report (step 1, independently), but only one of them wakes
       anyone, with every sibling's outcome already durably written and read
       back with a consistent query -- no window where a wake queries before
       a sibling's own report has landed.

    The caller does the actual wake (an async Lambda invoke); this only ever
    decides whether one should happen, and prepares what it should say.
    """
    trigger = run.get("trigger") or {}
    coordinator_run_id = trigger.get("coordinatorRunId")
    task_id = trigger.get("taskId")
    if not coordinator_run_id or not task_id:
        return None

    outcome = _CHILD_OUTCOME.get(state_value, "failed")
    # The structured half of what a coordinator sees: `_digest_line` already
    # folds this into the wake-up text, but keeping it on the row too means a
    # task's own compact result set is queryable directly (`GET /tasks/{id}`),
    # not only recoverable by re-reading a synthesis run's prose.
    artifact_ids = [a["artifactId"] for a in artifacts.list_for_run(store, run["runId"], limit=20)]
    try:
        store.update(K.task_pk(task_id), K.task_child_sk(run["runId"]), {
            "status": outcome, "endedAt": now_iso(), "summary": (summary or "")[:2000],
            "artifactIds": artifact_ids,
        }, expect={"status": "active"})
    except Conflict:
        return None

    metrics.emit("TaskChildCompleted", 1, dimensions={"Outcome": outcome},
                 taskId=task_id, childRunId=run["runId"], coordinatorRunId=coordinator_run_id)

    remaining = store.increment(K.task_pk(task_id), "META", "pendingChildren", -1)
    metrics.emit("TaskActiveChildren", max(0, remaining), taskId=task_id, childRunId=run["runId"])
    if remaining > 0:
        return None   # siblings still outstanding; whoever finishes last wakes the coordinator

    task = store.try_get(K.task_pk(task_id), "META")
    if task is None:
        return None

    # Consistent, not eventually-consistent: every sibling's own report (step
    # 1) is durably committed before its own decrement (program order within
    # that sibling's single invocation), and this decrement only observed
    # zero because every sibling's decrement already applied -- but an
    # eventually-consistent read immediately after could still return a
    # stale snapshot that predates one of those writes reaching this replica.
    children = store.query(K.task_pk(task_id), sk_prefix="CHILD#", limit=50, consistent=True)
    done = sum(1 for c in children if c.get("status") == "done")
    lines = "\n".join(_digest_line(store, c) for c in children)
    goal = (f"All {len(children)} of the tasks you handed off have finished "
           f"({done} succeeded). Here's what came back:\n{lines}\n\n"
           "Continue the task: hand off the next step, or post the result.")

    continuation = runs.create(
        store, agent_id=task["coordinatorAgentId"], thread_id=task["threadId"], goal=goal,
        trigger={"type": "child_completion", "taskId": task_id,
                 "childRunId": run["runId"], "childAgentId": run["agentId"],
                 "outcome": outcome, "childCount": len(children), "doneCount": done})

    store.update(K.task_pk(task_id), "META", {
        "coordinatorRunId": continuation["runId"],
        "status": "awaiting_synthesis",
        "gsi1sk": K.tasks_gsi1_sk("awaiting_synthesis", task.get("createdAt", now_iso())),
    })
    metrics.emit("TaskFanInWake", 1, taskId=task_id, coordinatorRunId=continuation["runId"],
                childCount=len(children), doneCount=done)
    return continuation


#: A coordinator run's terminal `RunState.value` -> the task-level outcome
#: recorded when that run is the one that actually finishes the task.
_TASK_OUTCOME = {
    "COMPLETED": "completed", "PARTIAL": "completed",
    "FAILED": "failed", "EXPIRED": "failed",
    "CANCELLED": "cancelled",
}


def close_task_if_finished(store: Store, run: dict, state_value: str) -> None:
    """A coordinator run that just settled may be the one that finishes its
    task -- but only if nothing it did on the way out reopened it.

    Checked against the task's *current* `coordinatorRunId`, not "did this
    run ever coordinate this task": a run that fanned out again before
    settling leaves `pendingChildren > 0`, so it does not close the task it
    just re-opened; a run whose task already moved on to a later
    continuation before this one finished settling is no longer that task's
    live coordinator, so it must not close a task another run now owns.
    Called for every settling run (`orchestrator._finish`); most have no
    task at all, and return on the first, cheapest check.
    """
    task_id = _task_id_for(run)
    task = store.try_get(K.task_pk(task_id), "META")
    if task is None or task.get("status") == "closed":
        return
    if task.get("coordinatorRunId") != run["runId"]:
        return
    if int(task.get("pendingChildren") or 0) > 0:
        return  # this settle itself fanned out again; the task is still open

    outcome = _TASK_OUTCOME.get(state_value, "failed")
    try:
        store.update(K.task_pk(task_id), "META", {
            "status": "closed", "outcome": outcome, "closedAt": now_iso(),
            "gsi1sk": K.tasks_gsi1_sk("closed", task.get("createdAt", now_iso())),
        }, expect={"coordinatorRunId": run["runId"]})
    except Conflict:
        pass  # lost a race to another settle deciding this differently
    else:
        metrics.emit("TaskClosed", 1, dimensions={"Outcome": outcome},
                     taskId=task_id, coordinatorRunId=run["runId"])


def list_artifacts_for_task(store: Store, task_id: str, *, limit_per_run: int = 50) -> list[dict]:
    """Every artifact a task's own runs have produced -- the coordinator's
    (task_id is that run's own id) plus every child's -- as a handful of
    indexed `artifacts.list_for_run` reads, not a stored array that could
    drift from what actually exists.

    Known gap, not worth the extra bookkeeping for what it would cover: an
    intermediate synthesis round -- a continuation run that itself created
    an artifact before being superseded by a *later* continuation -- is not
    reachable here, because `task["coordinatorRunId"]` only ever holds the
    *current* one. The original run (`task_id` itself) and every child are
    always covered; only artifacts from a middle round of a multi-round
    synthesis chain could be missed.
    """
    task = store.try_get(K.task_pk(task_id), "META")
    if task is None:
        return []
    run_ids = {task_id, task.get("coordinatorRunId") or task_id}
    children = store.query(K.task_pk(task_id), sk_prefix="CHILD#", limit=100)
    run_ids.update(c["runId"] for c in children if c.get("runId"))
    rows: list[dict] = []
    for run_id in run_ids:
        rows.extend(artifacts.list_for_run(store, run_id, limit=limit_per_run))
    rows.sort(key=lambda r: r.get("createdAt") or "", reverse=True)
    return rows
