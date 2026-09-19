"""HTTP API router.

One Lambda dispatching on path. At single-user scale this beats fifteen
microfunctions.

Every route runs behind the API Gateway Cognito JWT authorizer, and every
store call re-checks `ownerId`: the authorizer proves who you are, the
ownership check proves the row is yours.
"""

from __future__ import annotations

import json
import os
import re
import traceback

import boto3

from amazai import agentcore, approvals, keys as K, runs
from amazai.policy import Capability
from amazai.states import RunState
from amazai.store import Conflict, NotFound, Store, new_id, now_iso

CORS = {
    "content-type": "application/json",
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "authorization,content-type",
    "access-control-allow-methods": "GET,POST,PATCH,DELETE,OPTIONS",
}


def _resp(status: int, body) -> dict:
    return {"statusCode": status, "headers": CORS, "body": json.dumps(body, default=str)}


def _owner(event) -> str:
    claims = (((event.get("requestContext") or {}).get("authorizer") or {})
              .get("jwt") or {}).get("claims") or {}
    return claims.get("sub") or os.environ.get("OWNER_ID", "owner")


def handler(event, context):  # noqa: ARG001
    method = (event.get("requestContext", {}).get("http", {}).get("method")
              or event.get("httpMethod") or "GET").upper()
    path = (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"
    if method == "OPTIONS":
        return _resp(204, {})

    try:
        body = json.loads(event.get("body") or "{}")
    except ValueError:
        return _resp(400, {"error": "invalid JSON body"})

    store = Store(_owner(event))

    try:
        return _route(store, method, path, body, event)
    except NotFound as exc:
        return _resp(404, {"error": "not_found", "detail": str(exc)})
    except PermissionError as exc:
        return _resp(403, {"error": "forbidden", "detail": str(exc)})
    except Conflict as exc:
        return _resp(409, {"error": "conflict", "detail": str(exc)})
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return _resp(500, {"error": type(exc).__name__, "detail": str(exc)})


def _match(path: str, pattern: str) -> list[str] | None:
    rx = "^" + re.sub(r"\{(\w+)\}", r"([^/]+)", pattern) + "$"
    m = re.match(rx, path)
    return list(m.groups()) if m else None


def _route(store: Store, method: str, path: str, body: dict, event: dict):
    # --- agents ------------------------------------------------------------
    if path == "/agents" and method == "GET":
        return _resp(200, {"agents": store.query_index("gsi1", "gsi1pk", "AGENTS")})

    if (p := _match(path, "/agents/{id}")) and method == "GET":
        agent = store.get(K.agent_pk(p[0]), "META")
        agent["memory"] = store.query(K.agent_pk(p[0]), sk_prefix="MEM#")
        agent["grants"] = store.query(K.agent_pk(p[0]), sk_prefix="GRANT#")
        return _resp(200, agent)

    if (p := _match(path, "/agents/{id}")) and method == "PATCH":
        allowed = {"name", "role", "systemPrompt", "accent", "state",
                   "allowedTools", "budget", "model", "workspace", "preapproved"}
        changes = {k: v for k, v in body.items() if k in allowed}
        if not changes:
            return _resp(400, {"error": "no editable fields supplied"})
        return _resp(200, store.update(K.agent_pk(p[0]), "META", changes))

    # --- memory ------------------------------------------------------------
    if (p := _match(path, "/agents/{id}/memory")) and method == "POST":
        mem_id = new_id("mem_")
        return _resp(201, store.put({
            "pk": K.agent_pk(p[0]), "sk": K.memory_sk(mem_id),
            "entity": "Memory", "memId": mem_id,
            "title": body.get("title", ""), "body": body.get("body", ""),
            "source": "user", "pinned": bool(body.get("pinned", True)),
            "usedCount": 0,
        }))

    if (p := _match(path, "/agents/{id}/memory/{memId}")) and method == "DELETE":
        store.delete(K.agent_pk(p[0]), K.memory_sk(p[1]))
        return _resp(204, {})

    # --- threads -----------------------------------------------------------
    if path == "/threads" and method == "GET":
        return _resp(200, {"threads": store.query_index("gsi1", "gsi1pk", "THREADS")})

    if path == "/threads" and method == "POST":
        thread_id = new_id("th_")
        return _resp(201, store.put({
            "pk": K.thread_pk(thread_id), "sk": "META",
            "entity": "Thread", "threadId": thread_id,
            "gsi1pk": "THREADS", "gsi1sk": now_iso(),
            "kind": body.get("kind", "dm"),
            "title": body.get("title", "New task"),
            "agentIds": body.get("agentIds", []),
            "sessionId": K.session_id(thread_id),
            "lastActivity": now_iso(),
        }))

    if (p := _match(path, "/threads/{id}")) and method == "GET":
        thread = store.get(K.thread_pk(p[0]), "META")
        thread["messages"] = store.query(K.thread_pk(p[0]), sk_prefix="MSG#", limit=200)
        return _resp(200, thread)

    if (p := _match(path, "/threads/{id}/messages")) and method == "POST":
        return _post_message(store, p[0], body)

    if (p := _match(path, "/threads/{id}/exec")) and method == "POST":
        return _exec(store, p[0], body)

    # --- runs --------------------------------------------------------------
    if (p := _match(path, "/runs/{id}")) and method == "GET":
        run = store.get(K.run_pk(p[0]), "META")
        run["approvals"] = [approvals.to_card(a) for a in approvals.for_run(store, run["pk"])]
        run["events"] = store.query(run["pk"], sk_prefix="EVT#", limit=200)
        return _resp(200, run)

    if (p := _match(path, "/runs/{id}/cancel")) and method == "POST":
        run = store.get(K.run_pk(p[0]), "META")
        if RunState(run["state"]) in {RunState.COMPLETED, RunState.FAILED,
                                      RunState.CANCELLED, RunState.EXPIRED,
                                      RunState.PARTIAL}:
            return _resp(409, {"error": "run already finished", "state": run["state"]})
        # The in-flight tool call is allowed to finish; the orchestrator sees
        # this flag between events and settles the run.
        return _resp(202, runs.advance(store, run, RunState.CANCELLING))

    # --- approvals ---------------------------------------------------------
    if (p := _match(path, "/approvals/{runId}/{apvId}")) and method == "POST":
        return _decide(store, p[0], p[1], body)

    # --- usage -------------------------------------------------------------
    if path == "/usage" and method == "GET":
        qs = event.get("queryStringParameters") or {}
        agent_id = qs.get("agentId")
        month = qs.get("month") or now_iso()[:7]
        if not agent_id:
            return _resp(400, {"error": "agentId is required"})
        rows = store.query(K.cost_pk(agent_id, month), limit=500)
        total = sum(float(r.get("totalUsd", 0.0)) for r in rows)
        return _resp(200, {"agentId": agent_id, "month": month,
                           "totalUsd": round(total, 4), "runs": rows})

    return _resp(404, {"error": "no such route", "path": path, "method": method})


def _post_message(store: Store, thread_id: str, body: dict):
    text = (body.get("text") or "").strip()
    if not text:
        return _resp(400, {"error": "text is required"})

    thread = store.get(K.thread_pk(thread_id), "META")
    agent_id = body.get("agentId") or _pick_agent(thread, text)
    if not agent_id:
        return _resp(400, {"error": "no agent assigned to this thread"})

    store.put({
        "pk": K.thread_pk(thread_id), "sk": K.message_sk(now_iso(), new_id()[:8]),
        "entity": "Message", "role": "user", "author": "you", "text": text,
    })
    store.update(K.thread_pk(thread_id), "META", {"lastActivity": now_iso()})

    run = runs.create(store, agent_id=agent_id, thread_id=thread_id, goal=text)
    _invoke_orchestrator(run["runId"], store.owner_id)
    return _resp(202, {"runId": run["runId"], "state": run["state"]})


def _pick_agent(thread: dict, text: str) -> str | None:
    """An @mention wins in a room; otherwise the thread's first agent."""
    ids = thread.get("agentIds") or []
    for agent_id in ids:
        if f"@{agent_id}" in text:
            return agent_id
    return ids[0] if ids else None


def _exec(store: Store, thread_id: str, body: dict):
    """Raw shell in the microVM. No model, no tokens."""
    command = (body.get("command") or "").strip()
    if not command:
        return _resp(400, {"error": "command is required"})

    thread = store.get(K.thread_pk(thread_id), "META")
    agent_ids = thread.get("agentIds") or []
    if not agent_ids:
        return _resp(400, {"error": "no agent assigned to this thread"})
    agent = store.get(K.agent_pk(agent_ids[0]), "META")

    result = agentcore.AgentCore().exec(
        harness_arn=agent["harnessArn"],
        session_id=thread.get("sessionId") or K.session_id(thread_id),
        command=command,
    )
    return _resp(200, {
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "exitCode": result.get("exitCode", 0),
    })


def _decide(store: Store, run_id: str, approval_id: str, body: dict):
    run_pk = K.run_pk(run_id)
    run = store.get(run_pk, "META")

    if run["state"] != RunState.AWAITING_APPROVAL.value:
        return _resp(409, {"error": "run is not waiting on an approval",
                           "state": run["state"]})

    approve = bool(body.get("approve"))
    decided = approvals.decide(store, run_pk, approval_id,
                               approve=approve, note=body.get("note"))

    verb = "approved" if approve else "denied"
    note = f'Your decision on "{decided["action"]}": {verb}.'
    if body.get("note"):
        note += f" Note: {body['note']}"
    if not approve:
        note += " Do not attempt this action again; find another way or stop and explain."

    runs.advance(store, run, RunState.EXECUTING, pending=None)
    _invoke_orchestrator(run_id, store.owner_id, resume=True, resume_note=note)
    return _resp(200, {"approval": approvals.to_card(decided), "resumed": True})


def _invoke_orchestrator(run_id: str, owner_id: str, *, resume: bool = False,
                         resume_note: str = "") -> None:
    fn = os.environ.get("ORCHESTRATOR_FN_ARN")
    if not fn:
        return
    payload = {"runId": run_id, "ownerId": owner_id}
    if resume:
        payload.update({"resume": True, "resumeNote": resume_note})
    boto3.client("lambda").invoke(
        FunctionName=fn, InvocationType="Event",
        Payload=json.dumps(payload).encode(),
    )
