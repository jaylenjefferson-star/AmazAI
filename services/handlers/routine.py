"""Routine execution: scheduled, webhook, or manual.

EventBridge Scheduler is at-least-once, so the idempotency claim is not
optional: without it a doubled schedule does the work twice.
"""

from __future__ import annotations

import json
import os
import traceback

import boto3

from amazai import keys as K, runs
from amazai.store import Store, new_id, now_iso, ordered_suffix


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

    # Claim the fire. A second delivery returns the first run's ID.
    fired_at = event.get("scheduledTime") or now_iso()
    idem = event.get("idempotencyKey") or K.schedule_idempotency_key(routine_id, fired_at)
    run_id = new_id("run_")
    existing = store.claim(idem, run_id)
    if existing:
        return {"ok": True, "deduplicated": True, "runId": existing}

    thread_id = routine.get("threadId")
    if not thread_id:
        thread_id = new_id("th_")
        store.put({
            "pk": K.thread_pk(thread_id), "sk": "META",
            "entity": "Thread", "threadId": thread_id,
            "gsi1pk": "THREADS", "gsi1sk": now_iso(),
            "kind": "routine", "title": routine.get("name", "Routine"),
            "agentIds": [routine["agentId"]],
            "sessionId": K.session_id(thread_id),
            "lastActivity": now_iso(),
        })
        store.update(K.routine_pk(routine_id), "META", {"threadId": thread_id})

    prompt = routine.get("prompt") or routine.get("purpose", "")
    store.put({
        "pk": K.thread_pk(thread_id), "sk": K.message_sk(now_iso(), ordered_suffix()),
        "entity": "Message", "role": "user", "author": "routine",
        "routineId": routine_id, "text": prompt,
    })

    limits = routine.get("limits", {}) or {}
    run = runs.create(
        store, agent_id=routine["agentId"], thread_id=thread_id, goal=prompt,
        trigger={"type": routine.get("trigger", {}).get("type", "schedule"),
                 "routineId": routine_id, "idempotencyKey": idem},
        deadline_minutes=max(1, int(limits.get("maxDurationSec", 600)) // 60),
    )

    fn = os.environ.get("ORCHESTRATOR_FN_ARN")
    if fn:
        boto3.client("lambda").invoke(
            FunctionName=fn, InvocationType="Event",
            Payload=json.dumps({"runId": run["runId"], "ownerId": store.owner_id}).encode(),
        )

    store.update(K.routine_pk(routine_id), "META", {
        "lastRun": {"runId": run["runId"], "at": now_iso(), "status": "started"},
    })
    return {"ok": True, "runId": run["runId"]}
