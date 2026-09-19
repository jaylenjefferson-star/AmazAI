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

from amazai import approvals, keys as K, runs
from amazai.evidence import EvidenceWriter
from amazai.push import Push
from amazai.states import PAUSED, SWEEPABLE, RunState
from amazai.store import Store

STALE_MINUTES = 10


def handler(event, context):  # noqa: ARG001
    store = Store(os.environ.get("OWNER_ID", "owner"))
    push = Push(store)
    now = datetime.now(timezone.utc)
    result = {"approvalsExpired": 0, "runsResumed": 0, "runsFailed": 0, "runsExpired": 0}

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

    print(json.dumps(result))
    return result


def _sweep_run(store: Store, push: Push, run: dict, state: RunState,
               now: datetime, result: dict) -> None:
    # A paused run past its deadline expires. Its pending approvals were
    # already denied by step 1.
    if state in PAUSED:
        if runs.deadline_passed(run, now=now):
            _seal(store, push, run, RunState.EXPIRED,
                  "expired while waiting; pending approvals were denied")
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
              f"{pending.get('toolUseId')} is unknown and was not retried")
        result["runsFailed"] += 1
        return

    # No unresolved side effect: safe to resume on the same session, with the
    # agent's files and git state intact.
    fn = os.environ.get("ORCHESTRATOR_FN_ARN")
    if fn:
        boto3.client("lambda").invoke(
            FunctionName=fn, InvocationType="Event",
            Payload=json.dumps({"runId": run["runId"], "ownerId": store.owner_id,
                                "resume": True}).encode(),
        )
        result["runsResumed"] += 1


def _seal(store: Store, push: Push, run: dict, state: RunState, reason: str) -> None:
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
