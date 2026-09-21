"""Run record lifecycle: create, advance, pause, seal.

Holds the persistence rule from docs/architecture/05-run-lifecycle.md:
nothing irreversible happens until the intent to do it is durable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from amazai import compute as compute_mod
from amazai import keys as K
from amazai.compute import ComputeProvider, ComputeRequirements
from amazai.states import RunState, is_terminal, transition
from amazai.store import Store, new_id, now_iso

DEFAULT_DEADLINE_MINUTES = 15

#: Compute-status values recorded on ``run['compute']['status']``. These are a
#: field on the run row, NOT new RunStates: the run's RunState machine
#: (states.py) is unchanged, and acquiring/releasing compute is orthogonal to it
#: (an AgentCore run acquires ready compute instantly and never leaves its
#: existing flow). See docs/architecture/05-run-lifecycle.md.
COMPUTE_PENDING = "pending"
COMPUTE_ACQUIRING = "acquiring"
COMPUTE_READY = "ready"
COMPUTE_RELEASED = "released"


def _compute_block(requirements: ComputeRequirements | dict | None) -> dict:
    """Build the additive ``compute`` block stored on a new run.

    Absent input (``None``) means all-False requirements, so a run created
    without compute info behaves exactly as an AgentCore run and existing
    callers need no change. ``assignment`` stays ``None`` until the lifecycle
    resolves and records it; ``status`` starts ``pending``.
    """
    if requirements is None:
        requirements = ComputeRequirements()
    elif isinstance(requirements, dict):
        requirements = ComputeRequirements.from_run({"compute": {"requirements": requirements}})
    return {
        "requirements": requirements.as_flags(),
        "assignment": None,
        "status": COMPUTE_PENDING,
    }


def create(store: Store, *, agent_id: str, thread_id: str, goal: str,
           trigger: dict | None = None, deadline_minutes: int = DEFAULT_DEADLINE_MINUTES,
           run_id: str | None = None,
           compute_requirements: ComputeRequirements | dict | None = None) -> dict:
    """`run_id` is for a caller that has already *claimed* an id (an idempotency
    key for a routine fire): the id it stored must be the id the run has, or a
    retried caller is handed the identifier of a run that never existed.

    `compute_requirements` OPTIONALLY seeds the run's full-computer requirements
    (a ``ComputeRequirements`` or a flag dict); omitting it means all-False, so
    the run resolves to the default AgentCore runtime. This is additive and
    backward-compatible: existing callers and records are unaffected."""
    run_id = run_id or new_id("run_")
    deadline = datetime.now(timezone.utc) + timedelta(minutes=deadline_minutes)

    item = {
        "pk": K.run_pk(run_id), "sk": "META",
        "entity": "Run", "runId": run_id,
        "gsi1pk": "RUNS", "gsi1sk": now_iso(),
        "gsi2pk": K.run_state_gsi(RunState.QUEUED.value), "gsi2sk": now_iso(),
        "agentId": agent_id, "threadId": thread_id,
        "sessionId": K.session_id(thread_id),
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
        # Which compute substrate this run uses. Additive and backward
        # compatible: a run created without compute info records all-False
        # requirements (=> AgentCore) and no assignment yet. The lifecycle hooks
        # below fill in the resolved assignment and status via store.update on
        # this row, never a raw write that bypasses the store.
        "compute": _compute_block(compute_requirements),
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


# --- compute lifecycle hooks ------------------------------------------------
#
# The lifecycle is acquire -> wait_until_ready -> execute -> release. It reuses
# the EXISTING run mechanism and does NOT add a parallel run system or a new
# RunState: compute status lives on the run row (`compute.status`), written via
# store.update on the RUN# META row (owner-scoped, like `heartbeat`), and the
# resolved assignment is written the same way. `execute` is not a hook here --
# it is the orchestrator's existing model/tool loop; the hooks only ensure
# compute is ready before it runs and released after.
#
# For the default AGENTCORE provider every hook is effectively a no-op over live
# infrastructure: the provider's acquire returns an immediately-ready handle and
# release does nothing, so an AgentCore run behaves EXACTLY as before. For a run
# that resolves to EC2_DESKTOP the hooks call the placeholder provider, whose
# methods raise NotImplementedError this phase (no EC2 is provisioned).


def _write_compute(store: Store, run: dict, changes: dict) -> dict:
    """Merge changes into ``run['compute']`` and persist the whole sub-object.

    Writes go through ``store.update`` on the RUN# META row (owner-scoped),
    never a raw table write and never touching ``state``, so the run's RunState
    machine is undisturbed. Returns the updated run.
    """
    block = dict(run.get("compute") or {})
    block.update(changes)
    return store.update(run["pk"], "META", {"compute": block})


def select_and_record_compute(store: Store, run: dict, agent: dict | None = None) -> dict:
    """Resolve the compute assignment for a run and persist it on the run row.

    Pure selection (no AWS) via ``compute.select_for_run``; the frozen
    assignment is written onto ``run['compute']['assignment']`` via
    ``store.update``. Idempotent: if an assignment is already recorded it is
    returned unchanged. Returns the updated run.
    """
    existing = (run.get("compute") or {}).get("assignment")
    if existing:
        return run
    assignment = compute_mod.select_for_run(run=run, agent=agent)
    return _write_compute(store, run, {"assignment": assignment.as_record()})


def _provider_for(run: dict, **deps) -> object:
    """Build the live provider for a run's recorded (or resolved) assignment."""
    assignment = (run.get("compute") or {}).get("assignment")
    if assignment:
        provider_enum = ComputeProvider(assignment["provider"])
    else:
        provider_enum = compute_mod.select_for_run(run=run).provider
    return compute_mod.get_provider(provider_enum, **deps)


def acquire_compute(store: Store, run: dict, agent: dict | None = None, *,
                    provider: object | None = None, **deps) -> tuple[dict, object, object]:
    """Acquire compute for a run before execution begins.

    Resolves and records the assignment (if not already), builds the provider
    (or uses an injected one), records status ``acquiring``, then calls
    ``provider.acquire``. For AGENTCORE this returns an immediately-ready handle
    and the run stays in its existing flow. For EC2_DESKTOP the placeholder
    ``acquire`` raises NotImplementedError (no provisioning this phase).

    Returns ``(run, provider, handle)``.
    """
    run = select_and_record_compute(store, run, agent)
    if provider is None:
        provider = _provider_for(run, **deps)
    run = _write_compute(store, run, {"status": COMPUTE_ACQUIRING})
    handle = provider.acquire(run, agent or {}, store=store)
    return run, provider, handle


def wait_until_ready(store: Store, run: dict, provider: object, handle: object) -> dict:
    """Block until the acquired compute is ready, then record status ``ready``.

    For AGENTCORE ``provider.wait_until_ready`` returns immediately (always
    ready). Returns the updated run.
    """
    provider.wait_until_ready(handle, store=store)
    return _write_compute(store, run, {"status": COMPUTE_READY})


def release_compute(store: Store, run: dict, provider: object | None = None,
                    handle: object | None = None, **deps) -> dict:
    """Release the compute and record status ``released``.

    Reachable on success, failure, AND cancellation (matching the "sync runs on
    failure and cancellation too" rule in docs/architecture/04-workspaces.md).
    For AGENTCORE ``provider.release`` is a no-op. If no provider is supplied it
    is built from the run's recorded assignment. Returns the updated run.
    """
    if provider is None:
        provider = _provider_for(run, **deps)
    provider.release(handle, store=store)
    return _write_compute(store, run, {"status": COMPUTE_RELEASED})
