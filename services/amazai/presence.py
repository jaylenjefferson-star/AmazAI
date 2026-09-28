"""Presence derived from durable state, not from the live socket.

`web/src/presence.js` is a socket-fed cache: correct while a browser is
connected, but empty on reload and unable to answer "what is the org doing
right now" once the client is gone. The push events it folds are a live
mirror of run-state transitions the control plane already persists as RUN#
rows, so the same answer can be *read back* from those rows at any time.

This module is that read. `derive(store)` walks the agents, the non-terminal
RUN# rows and the open TASK# rows that already exist and returns one presence
descriptor per active Companion, using the SAME RunState -> presence-state
mapping the frontend encodes in `presence.js` RUN_STATES so the endpoint and
the live socket never disagree. It is a pure derivation over rows -- no writes,
no model calls -- matching the `directory.py` / `collab.active_run_count_for_*`
convention, so it is cheap to poll and safe when nothing is running.

The seam that folds a snapshot into the live store on load is
`presence.seed()` on the frontend; this is only its data source.
"""

from __future__ import annotations

from amazai import agents as A, artifacts as AR, keys as K
from amazai.states import TERMINAL
from amazai.store import Store

_TERMINAL_VALUES = frozenset(s.value for s in TERMINAL)

#: RunState value -> (presence state, hover line). The presence states and the
#: text mirror `web/src/presence.js` RUN_STATES exactly, with one rename that
#: is only a naming choice: the frontend calls the "asking you" bucket
#: `approval`; here it is `needs_approval`, which `presence.seed()` maps back to
#: `approval`. Keeping the two tables in step is what lets a reload-seeded
#: Companion light up identically to one driven by a live socket event -- a
#: divergence here would show up as a Bot that looks busy over the socket but
#: idle after refresh, or the reverse.
_RUN_STATES: dict[str, tuple[str, str]] = {
    "QUEUED":             ("thinking", "Getting started"),
    "PLANNING":           ("thinking", "Planning"),
    "EXECUTING":          ("thinking", ""),
    "RETRYING":           ("working", "Retrying"),
    "AWAITING_APPROVAL":  ("needs_approval", "Waiting for your approval"),
    "AWAITING_INPUT":     ("needs_approval", "Waiting for your answer"),
    "AWAITING_LOGIN":     ("needs_approval", "Waiting for you to sign in"),
    "AWAITING_CONNECTOR": ("waiting", "Waiting on a connector"),
    "SUSPENDED":          ("waiting", "Paused"),
    "CANCELLING":         ("waiting", "Stopping"),
}


def state_for_run(run_state: str) -> tuple[str, str]:
    """Map one RunState value to (presence state, action line).

    A terminal or unknown state has no presence: the caller only ever looks at
    non-terminal runs, so this returning ("idle", "") for anything else is a
    backstop, not a path the assembler relies on. Kept pure so the mapping is
    testable without a table.
    """
    return _RUN_STATES.get(run_state, ("idle", ""))


def _run_sort_key(run: dict) -> str:
    """Newest-run-first ordering. `startedAt` is set at create time and is the
    natural "when did this begin"; `gsi1sk` (also a create-time stamp) is the
    fallback for a row that predates it. Empty string sorts oldest, so a run
    missing both simply loses to any run that has one."""
    return run.get("startedAt") or run.get("gsi1sk") or ""


def assemble(agents: list[dict], runs: list[dict], tasks: list[dict]) -> list[dict]:
    """Build the presence snapshot from already-queried rows.

    Split out from `derive` so the whole derivation is testable without a store
    and provably read-only: it takes rows and returns descriptors, touching
    nothing. For each active/seated agent it finds the most-recent non-terminal
    run and maps its state; an agent with no such run is idle and is omitted (a
    reader treats absence as idle, exactly as `presence.js put()` does). If the
    agent coordinates an open task with pending children, its action is
    overlaid with the wait so a resting-looking coordinator reads as blocked on
    its teammates rather than idle.
    """
    # Most-recent non-terminal run per agent, in one pass over the RUNS listing.
    latest_run: dict[str, dict] = {}
    for run in runs:
        if run.get("state") in _TERMINAL_VALUES:
            continue
        agent_id = run.get("agentId")
        if not agent_id:
            continue
        current = latest_run.get(agent_id)
        if current is None or _run_sort_key(run) > _run_sort_key(current):
            latest_run[agent_id] = run

    # Open-task fan-out per coordinator: how many teammates it is waiting on. A
    # task is open while its status is anything but a terminal "closed" one, and
    # `pendingChildren` is the same live counter the fan-in decrements to zero.
    pending_by_coordinator: dict[str, int] = {}
    for task in tasks:
        if task.get("status") in {"closed", "cancelled"}:
            continue
        pending = int(task.get("pendingChildren") or 0)
        if pending <= 0:
            continue
        coordinator = task.get("coordinatorAgentId")
        if not coordinator:
            continue
        pending_by_coordinator[coordinator] = pending_by_coordinator.get(coordinator, 0) + pending

    snapshot: list[dict] = []
    for agent in agents:
        # Only surface Bots that hold a seat -- a provisioning or failed row is
        # not something a person is watching for motion.
        if agent.get("status", agent.get("state")) not in A.SEATED:
            continue
        agent_id = agent.get("agentId")
        if not agent_id:
            continue

        run = latest_run.get(agent_id)
        if run is not None:
            state, action = state_for_run(run["state"])
            descriptor = {
                "agentId": agent_id,
                "name": agent.get("name") or agent_id,
                "state": state,
                "action": action,
                "runId": run.get("runId"),
                "threadId": run.get("threadId"),
                "since": run.get("startedAt") or run.get("gsi1sk"),
            }
        else:
            descriptor = {
                "agentId": agent_id,
                "name": agent.get("name") or agent_id,
                "state": "idle",
                "action": "",
                "runId": None,
                "threadId": None,
                "since": None,
            }

        pending = pending_by_coordinator.get(agent_id)
        if pending:
            # A coordinator whose own run is parked (or done) still owes an
            # answer while children are outstanding; say so rather than letting
            # it look idle. Overlaid on the action only -- the run-derived state
            # is left intact so a coordinator that is itself executing still
            # reads as thinking/working.
            teammates = "teammate" if pending == 1 else "teammates"
            descriptor["action"] = f"Waiting on {pending} {teammates}"
            if descriptor["state"] == "idle":
                descriptor["state"] = "waiting"

        # Idle-with-nothing-to-say is absence: the frontend store treats a
        # missing agent as idle, so surfacing it would only add noise.
        if descriptor["state"] == "idle":
            continue
        snapshot.append(descriptor)

    return snapshot


def derive(store: Store) -> list[dict]:
    """The read behind GET /presence: a durable-state presence snapshot.

    Reuses the same gsi1 `RUNS` / `AGENTS` / `TASKS` listings the rest of the
    codebase already reads (`collab.active_run_count_for_agent`, `api` GET
    /agents and GET /tasks) rather than adding a GSI -- moto is strict about
    the table's declared indexes and a new one would be a schema change with no
    payoff here. Purely a read; the assembly lives in `assemble` so nothing on
    this path can write.
    """
    agents = store.query_index("gsi1", "gsi1pk", "AGENTS", limit=200)
    runs = store.query_index("gsi1", "gsi1pk", "RUNS", limit=1000)
    tasks = store.query_index("gsi1", "gsi1pk", "TASKS", limit=200)
    return assemble(agents, runs, tasks)


#: The room-coordination view spells its member states with the SAME buckets
#: `assemble` uses (idle/thinking/working/waiting/needs_approval), so the room
#: card and the per-Companion presence dot never disagree about one Bot. Only
#: the room-level flags below (handoff/artifact/stage owner) are new here.
def assemble_room(agents: list[dict], runs: list[dict], *,
                  handoff_seen: bool, artifact_seen: bool) -> dict:
    """The durable coordination state of ONE room, from rows already queried.

    Split from `room_coordination` for the same reason `assemble` is split from
    `derive`: it is a pure function of the rows the caller already holds, so the
    whole derivation is testable without a store and provably read-only. It
    answers the four things a person watching a room wants to know that a raw
    unattributed delta stream cannot say -- who owns the current stage, what
    each member is doing (working/waiting/needs-approval), whether a handoff has
    happened, and whether the team has produced a deliverable -- and every one
    of them is read back from the run/task/handoff/artifact rows the control
    plane already persists, never from a live model.

    `agents` and `runs` are already narrowed to this room's members and this
    thread's runs by the caller; `handoff_seen`/`artifact_seen` are the two
    room-level booleans that come from HOFF# rows and per-run artifact reads,
    passed in so this stays a pure fold.
    """
    # Most-recent non-terminal run per member, one pass -- same rule `assemble`
    # uses so a member reads identically here and on its own Companion.
    latest_run: dict[str, dict] = {}
    for run in runs:
        if run.get("state") in _TERMINAL_VALUES:
            continue
        agent_id = run.get("agentId")
        if not agent_id:
            continue
        current = latest_run.get(agent_id)
        if current is None or _run_sort_key(run) > _run_sort_key(current):
            latest_run[agent_id] = run

    members: list[dict] = []
    for agent in agents:
        agent_id = agent.get("agentId")
        if not agent_id:
            continue
        run = latest_run.get(agent_id)
        if run is not None:
            state, action = state_for_run(run["state"])
        else:
            state, action = "idle", ""
        members.append({
            "agentId": agent_id,
            "name": agent.get("name") or agent_id,
            "state": state,
            "action": action,
            "runId": run.get("runId") if run else None,
            "since": (run.get("startedAt") or run.get("gsi1sk")) if run else None,
        })

    # Stage owner: whoever is actively in a turn on the newest run, so the room
    # header can say "X is on it" rather than leaving the current step ownerless.
    # A member merely waiting or awaiting approval is not "owning the stage"; it
    # is parked. If nobody is actively working, there is no owner to name.
    owner = None
    owner_since = ""
    for member in members:
        if member["state"] not in {"thinking", "working"}:
            continue
        since = member["since"] or ""
        if owner is None or since > owner_since:
            owner, owner_since = member, since

    any_working = any(m["state"] in {"thinking", "working"} for m in members)
    any_waiting = any(m["state"] == "waiting" for m in members)
    needs_approval = any(m["state"] == "needs_approval" for m in members)

    return {
        "members": members,
        "stageOwnerAgentId": owner["agentId"] if owner else None,
        "working": any_working,
        "waiting": any_waiting,
        "needsApproval": needs_approval,
        "handoffOccurred": handoff_seen,
        "artifactProduced": artifact_seen,
    }


def room_coordination(store: Store, thread_id: str) -> dict:
    """The read behind a room's coordination view.

    Reuses the same listings the coordination endpoint and `derive` already
    read -- the gsi1 `RUNS`/`AGENTS` listings, the per-run HOFF# rows, and the
    gsi2 per-run artifact index -- so it adds no GSI (moto is strict about the
    table's declared indexes) and no model call. Purely a read; the fold lives
    in `assemble_room` so nothing on this path can write.

    A room whose thread row is missing returns an empty view rather than
    raising: the caller (the console) treats absence of coordination as "no
    team activity yet", the same way it treats an empty presence snapshot.
    """
    thread = store.try_get(K.thread_pk(store.owner_id, thread_id), "META")
    if thread is None:
        return {"members": [], "stageOwnerAgentId": None, "working": False,
                "waiting": False, "needsApproval": False,
                "handoffOccurred": False, "artifactProduced": False}

    member_ids = [a for a in (thread.get("agentIds") or []) if a]
    by_id = {a["agentId"]: a for a in store.query_index("gsi1", "gsi1pk", "AGENTS", limit=200)
             if a.get("agentId")}
    members = [by_id.get(mid, {"agentId": mid, "name": mid}) for mid in member_ids]

    thread_runs = [r for r in store.query_index("gsi1", "gsi1pk", "RUNS", limit=1000)
                   if r.get("threadId") == thread_id]

    # A handoff or an artifact on ANY run this thread has driven is a room-level
    # fact: it says the team coordinated or produced something here, without
    # attributing a live delta to a single Bot (which a room deliberately never
    # does). Both are read straight off durable rows.
    handoff_seen = any(
        store.query(r["pk"], sk_prefix="HOFF#", limit=1) for r in thread_runs)
    artifact_seen = any(
        AR.list_for_run(store, r["runId"], limit=1) for r in thread_runs if r.get("runId"))

    return assemble_room(members, thread_runs,
                         handoff_seen=handoff_seen, artifact_seen=artifact_seen)
