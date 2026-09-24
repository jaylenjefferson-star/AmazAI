"""Recovery sweep, every five minutes.

Two jobs, both from docs/architecture/05-run-lifecycle.md:

  1. Expire pending approvals. An undecided approval is stale authority, so it
     expires to DENIED -- never to a silent grant.
  2. Recover runs whose worker died.

The second job has one rule that matters more than the rest: a run that died
with a tool call in flight is NOT retried. The call may already have taken
effect, and the honest response to that ambiguity is to surface it, not to
resolve it by guessing.
"""

from __future__ import annotations

import json
import os
import traceback
from datetime import datetime, timezone

import boto3

from amazai import approvals, handoffs, keys as K, metrics, runs
from amazai.evidence import EvidenceWriter
from amazai.push import Push
from amazai.states import PAUSED, SWEEPABLE, RunState
from amazai.store import Store, discover_owner_ids

STALE_MINUTES = 10

_RESULT_KEYS = ("approvalsExpired", "runsResumed", "runsFailed", "runsExpired")


def handler(event, context):  # noqa: ARG001
    """Sweep every owner, not one hardcoded default.

    A scheduled invocation carries no request principal, so there is no
    `ownerId` to read the way `api.py` reads one from the Auth0 token. The
    previous shape -- `Store(os.environ.get("OWNER_ID", "owner"))` -- silently
    fell back to the literal string `"owner"`, which matches no real tenant's
    id (a Google/Auth0 subject like `google-oauth2|...`); every query it made
    was owner-filtered against a value nothing was ever written under, so
    every sweep, forever, resumed and expired nothing. `event.get("ownerId")`
    still lets a manual or test invocation target one owner; otherwise every
    owner with at least one Bot is discovered and swept in turn.
    """
    now = datetime.now(timezone.utc)
    explicit = event.get("ownerId") or os.environ.get("OWNER_ID")
    owner_ids = [explicit] if explicit else discover_owner_ids()

    total = {"owners": len(owner_ids), **{k: 0 for k in _RESULT_KEYS}}
    for owner_id in owner_ids:
        store = Store(owner_id)
        push = Push(store)
        try:
            result = _sweep_owner(store, push, now)
        except Exception:  # noqa: BLE001 -- one owner's failure must not stop the rest
            traceback.print_exc()
            continue
        for key in _RESULT_KEYS:
            total[key] += result[key]

    print(json.dumps(total))
    for key in _RESULT_KEYS:
        metrics.emit(f"Sweep{key[0].upper()}{key[1:]}", total[key], owners=total["owners"])
    return total


def _sweep_owner(store: Store, push: Push, now: datetime) -> dict:
    result = {k: 0 for k in _RESULT_KEYS}

    # 1. Approvals past their deadline.
    for approval in approvals.due_for_expiry(store, now=now):
        if approval.get("status") != approvals.PENDING:
            continue
        try:
            approvals.expire(store, approval["pk"], approval["approvalId"])
            result["approvalsExpired"] += 1
            push.notification(
                "warn",
                f'Approval for "{approval.get("action")}" expired and was denied.',
                runId=approval.get("runId"),
            )
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    # 2. Runs in a non-terminal state.
    for state in sorted(SWEEPABLE, key=lambda s: s.value):
        cutoff = now.isoformat(timespec="seconds").replace("+00:00", "Z")
        try:
            stale = store.query_index(
                "gsi2", "gsi2pk", K.run_state_gsi(state.value),
                sk_name="gsi2sk", sk_lt=cutoff, limit=25,
            )
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            continue

        for run in stale:
            try:
                _sweep_run(store, push, run, state, now, result)
            except Exception:  # noqa: BLE001
                traceback.print_exc()

    return result


def _sweep_run(store: Store, push: Push, run: dict, state: RunState,
               now: datetime, result: dict) -> None:
    # A paused run past its deadline expires. Its pending approvals were
    # already denied by step 1.
    if state in PAUSED:
        if runs.deadline_passed(run, now=now):
            _seal(store, push, run, RunState.EXPIRED,
                  "expired while waiting; pending approvals were denied",
                  reason_code="approval_expired")
            result["runsExpired"] += 1
        return

    if not runs.heartbeat_stale(run, minutes=STALE_MINUTES, now=now):
        return

    pending = run.get("pending")
    if pending and pending.get("kind") == "tool":
        # The worker died between issuing a tool call and recording its result.
        # The action may or may not have happened. Never silently retry it.
        _seal(store, push, run, RunState.FAILED,
              "worker failed with a tool call in flight; the outcome of "
              f"{pending.get('toolUseId')} is unknown and was not retried",
              reason_code="tool_call_in_flight")
        result["runsFailed"] += 1
        return

    if runs.deadline_passed(run, now=now):
        # A stale heartbeat alone is not damning -- the worker may simply be
        # between invocations, and resuming it is exactly what heals that. But
        # past its own deadline *too*, another resume is not a second chance,
        # it is the same failure mode repeating on a timer: five real runs
        # were found stuck in RETRYING for days, resumed on paper every five
        # minutes forever because nothing ever asked whether resuming had
        # actually been working. Declared dead here instead, so a task
        # waiting on this run as a child
        # (`handoffs.notify_coordinator_if_child`, wired into `_seal` below)
        # is told rather than left waiting on a run that will never settle.
        _seal(store, push, run, RunState.FAILED,
              "timed out: no heartbeat and past its deadline; presumed stuck "
              "and stopped rather than resumed indefinitely",
              reason_code="heartbeat_and_deadline")
        result["runsFailed"] += 1
        return

    # No unresolved side effect and still inside its deadline: safe to
    # resume on the same session, with the agent's files and git state intact.
    fn = os.environ.get("ORCHESTRATOR_FN_ARN")
    if fn:
        boto3.client("lambda").invoke(
            FunctionName=fn, InvocationType="Event",
            Payload=json.dumps({"runId": run["runId"], "ownerId": store.owner_id,
                                "resume": True}).encode(),
        )
        result["runsResumed"] += 1


def _seal(store: Store, push: Push, run: dict, state: RunState, reason: str, *,
         reason_code: str = "other") -> None:
    ev = EvidenceWriter(run["runId"])
    ev.error(run.get("cursor", {}).get("lastEventSeq", 0), "sweeper", reason)
    manifest = ev.seal(
        run=run, outcome=state.value, summary=reason,
        cost={"totalUsd": run.get("costUsd", 0.0)},
        approvals=approvals.for_run(store, run["pk"]),
    )
    runs.advance(store, run, state, evidenceKey=ev.key, summary=reason,
                 sealSha256=manifest.get("sealSha256"))
    push.run_end(run["runId"], run["threadId"], state.value, reason,
                 run.get("costUsd", 0.0))
    metrics.emit("RunSweepSealed", 1, dimensions={"State": state.value, "Reason": reason_code},
                runId=run["runId"], agentId=run.get("agentId", ""))

    # A swept run can be a coordinator's child too -- the worker that died
    # mid tool-call, or the run that finally expired past its deadline. Same
    # idempotent hook `_finish`/`_fail` use, so a duplicate settle here (this
    # sweep racing a live invocation's own settle of the same run) still
    # wakes the coordinator at most once.
    try:
        fn = os.environ.get("ORCHESTRATOR_FN_ARN")
        continuation_run = handoffs.notify_coordinator_if_child(store, run, state.value, reason)
        if continuation_run and fn:
            boto3.client("lambda").invoke(
                FunctionName=fn, InvocationType="Event",
                Payload=json.dumps({"runId": continuation_run["runId"],
                                    "ownerId": store.owner_id}).encode(),
            )
    except Exception:  # noqa: BLE001
        traceback.print_exc()

    # Same reasoning as the child-report above, for the other half of
    # `orchestrator._wake_coordinator_if_child`: a swept run can also be a
    # task's own coordinator finishing (or dying) rather than one of its
    # children.
    try:
        handoffs.close_task_if_finished(store, run, state.value)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
