"""WebSocket handler: connect, disconnect, and inbound messages.

The socket carries deltas, tool chips, approval prompts and run state out to
the console; inbound it accepts `{action:"send", threadId, text}` so you can
talk to an agent without a round trip through the HTTP API.
"""

from __future__ import annotations

import json
import os
import time
import traceback

import boto3

from amazai import keys as K, runs
from amazai.store import Store, new_id, now_iso

CONNECTION_TTL_HOURS = 12


def _owner(event) -> str:
    claims = (((event.get("requestContext") or {}).get("authorizer") or {})
              .get("jwt") or {}).get("claims") or {}
    return claims.get("sub") or os.environ.get("OWNER_ID", "owner")


def handler(event, context):  # noqa: ARG001
    ctx = event.get("requestContext") or {}
    route = ctx.get("routeKey")
    conn_id = ctx.get("connectionId", "")
    store = Store(_owner(event))

    try:
        if route == "$connect":
            store.put({
                "pk": K.connection_pk(conn_id), "sk": "META",
                "entity": "Connection", "connectionId": conn_id,
                "gsi1pk": "CONNS", "gsi1sk": now_iso(),
                "ttl": int(time.time()) + CONNECTION_TTL_HOURS * 3600,
            })
            return {"statusCode": 200}

        if route == "$disconnect":
            try:
                store.delete(K.connection_pk(conn_id), "META")
            except Exception:  # noqa: BLE001
                pass
            return {"statusCode": 200}

        return _default(store, event)

    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return {"statusCode": 500, "body": str(exc)}


def _default(store: Store, event: dict) -> dict:
    try:
        body = json.loads(event.get("body") or "{}")
    except ValueError:
        return {"statusCode": 400, "body": "invalid JSON"}

    if body.get("action") != "send":
        return {"statusCode": 200}

    thread_id = body.get("threadId")
    text = (body.get("text") or "").strip()
    if not thread_id or not text:
        return {"statusCode": 400, "body": "threadId and text are required"}

    thread = store.get(K.thread_pk(thread_id), "META")
    agent_ids = thread.get("agentIds") or []
    # An @mention wins in a room; otherwise the thread's first agent.
    agent_id = next((a for a in agent_ids if f"@{a}" in text), agent_ids[0] if agent_ids else None)
    if not agent_id:
        return {"statusCode": 400, "body": "no agent assigned to this thread"}

    store.put({
        "pk": K.thread_pk(thread_id), "sk": K.message_sk(now_iso(), new_id()[:8]),
        "entity": "Message", "role": "user", "author": "you", "text": text,
    })
    store.update(K.thread_pk(thread_id), "META", {"lastActivity": now_iso()})

    run = runs.create(store, agent_id=agent_id, thread_id=thread_id, goal=text)

    fn = os.environ.get("ORCHESTRATOR_FN_ARN")
    if fn:
        boto3.client("lambda").invoke(
            FunctionName=fn, InvocationType="Event",
            Payload=json.dumps({"runId": run["runId"], "ownerId": store.owner_id}).encode(),
        )
    return {"statusCode": 200}
