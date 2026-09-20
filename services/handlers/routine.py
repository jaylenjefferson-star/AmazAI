"""Routine execution: scheduled, webhook, or manual.

EventBridge Scheduler is at-least-once, so the idempotency claim is not
optional: without it a doubled schedule does the work twice. The claim, the
thread and the run are `routines.fire`, shared with "Run now" in the API.
"""

from __future__ import annotations

import json
import os
import traceback

import boto3

from amazai import keys as K, routines
from amazai.store import Store, now_iso


def _invoke(store: Store):
    def go(run_id: str) -> None:
        fn = os.environ.get("ORCHESTRATOR_FN_ARN")
        if fn:
            boto3.client("lambda").invoke(
                FunctionName=fn, InvocationType="Event",
                Payload=json.dumps({"runId": run_id, "ownerId": store.owner_id}).encode(),
            )
    return go


def handler(event, context):  # noqa: ARG001
    store = Store(event.get("ownerId") or os.environ.get("OWNER_ID", "owner"))
    routine_id = event.get("routineId")
    if not routine_id:
        return {"ok": False, "error": "routineId is required"}

    try:
        routine = store.get(K.routine_pk(routine_id), "META")
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return {"ok": False, "error": "routine not found"}

    if not routine.get("enabled", True):
        return {"ok": False, "skipped": "routine is disabled"}

    return routines.fire(
        store, routine, invoke=_invoke(store),
        fired_at=event.get("scheduledTime") or now_iso(),
        idempotency_key=event.get("idempotencyKey"),
    )
