"""HTTP API router.

One Lambda dispatching on path. At single-user scale this beats fifteen
microfunctions.

Every request presents an Auth0 access token. API Gateway checks it at the
edge and this handler verifies it again before deriving the owner from its
subject. The store then re-checks ``ownerId`` on every row operation.
"""

from __future__ import annotations

import json
import os
import re
import traceback

import boto3

from amazai import (agentcore, agents as A, approvals, collab, connectors as C,
                    identity, keys as K, memory, models, pipedream, routines as R,
                    runs, schedules, settings as S, skills, threads)
from amazai import dispatch
from amazai.policy import Capability
from amazai.states import PAUSED, RunState, TERMINAL
from amazai.store import Conflict, NotFound, Store, new_id, now_iso, ordered_suffix

CORS = {
    "content-type": "application/json",
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "authorization,content-type",
    "access-control-allow-methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
}


def _resp(status: int, body) -> dict:
    return {"statusCode": status, "headers": CORS, "body": json.dumps(body, default=str)}


def _owner(event) -> str:
    return _principal(event).user_id


def _principal(event) -> identity.Principal:
    """Return the one verified principal for this request.

    API Gateway's JWT authorizer is a valuable first gate, but handler code
    must not turn its context map into a second, weaker identity source. The
    raw bearer token is verified by ``identity`` and cached only for this
    in-memory Lambda invocation.
    """
    principal = event.get("_amazai_principal")
    if principal is None:
        principal = identity.principal_from_event(event)
        identity.assert_owner(principal)
        event["_amazai_principal"] = principal
    return principal


def _actor(event) -> A.Actor:
    """Who is asking.

    `agent_id` is always None here, and that is a property of this entry
    point rather than an omission: every route runs behind Auth0 validation,
    so the caller is a person. `agents.Actor` carries the field
    because the orchestrator calls the same validation functions on an agent's
    behalf, where the answer is not None.
    """
    principal = _principal(event)
    return A.Actor(user_id=principal.user_id, org_id=principal.org_id)


def _header(event, name: str) -> str:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    return headers.get(name.lower(), "")


_pd_client = None


def _pipedream():
    """One client per container. Built on first use so a request that never
    touches a connector never reads the secret."""
    global _pd_client
    if _pd_client is None:
        _pd_client = pipedream.Pipedream()
    return _pd_client


def _org_connectors(store: Store) -> dict[str, A.OrgConnector]:
    """Connectors the organization has installed, keyed by id.

    The ceiling for every per-agent grant. Empty until a connector is
    installed, which is why a grant request on a fresh org is refused rather
    than quietly granted — there is nothing yet to grant from.
    """
    out: dict[str, A.OrgConnector] = {}
    for row in store.query_index("gsi1", "gsi1pk", "CONNECTORS", limit=200):
        if row.get("status") not in (None, "installed", "authorized"):
            continue
        try:
            capability = Capability(row.get("capability", "read"))
        except ValueError:
            continue
        out[row["connectorId"]] = A.OrgConnector(
            connector_id=row["connectorId"],
            allowed_tools=frozenset(row.get("allowedTools") or []),
            capability=capability,
        )
    return out


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

    try:
        principal = _principal(event)
        store = Store(principal.user_id)
        identity.ensure_user(store, principal)
        return _route(store, method, path, body, event)
    except identity.AuthError:
        return _resp(401, {"error": "unauthorized"})
    except A.ValidationError as exc:
        return _resp(400, {"error": "invalid_request", "detail": str(exc)})
    except skills.ValidationError as exc:
        return _resp(400, {"error": "invalid_request", "detail": str(exc)})
    except memory.ValidationError as exc:
        return _resp(400, {"error": "invalid_request", "detail": str(exc)})
    except A.Escalation as exc:
        return _resp(403, {"error": "forbidden", "detail": str(exc)})
    except (C.NotInstalled, C.NotGranted, pipedream.TargetNotAllowed) as exc:
        return _resp(403, {"error": "forbidden", "detail": str(exc)})
    except C.UnknownConnector as exc:
        return _resp(404, {"error": "not_found", "detail": str(exc)})
    except pipedream.PipedreamError as exc:
        # The connector is reachable or it is not; either way this is not a
        # fault in the caller's request.
        return _resp(502, {"error": "connector_unavailable", "detail": str(exc)})
    except A.QuotaExceeded as exc:
        return _resp(409, {"error": "quota_exceeded", "detail": str(exc)})
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
        rows = store.query_index("gsi1", "gsi1pk", "AGENTS", limit=200)
        # Archived agents are excluded by default. They still exist, and their
        # evidence still resolves; they are simply not part of the org you are
        # operating today.
        qs = event.get("queryStringParameters") or {}
        wanted = qs.get("status")
        if wanted:
            rows = [r for r in rows if r.get("status", r.get("state")) == wanted]
        else:
            rows = [r for r in rows
                    if r.get("status", r.get("state")) not in {"archived", "failed"}]
        return _resp(200, {"agents": rows})

    if path == "/agents" and method == "POST":
        return _create_agent(store, body, event)

    if path == "/agents/options" and method == "GET":
        # The vocabulary the Create-a-Bot form offers, served from the same
        # constants the validator enforces. A second copy in the console would
        # drift, and the first sign of the drift would be a form that offers a
        # colour the API refuses.
        return _resp(200, {
            "shapes": list(A.AVATAR_SHAPES),
            "colors": list(A.AVATAR_COLORS),
            "workingStyles": list(A.WORKING_STYLES),
            "modelTiers": [
                {"key": tier, "ladder": ladder,
                 "maxTokens": models.TIER_MAX_TOKENS[tier],
                 "effort": models.TIER_EFFORT[tier]}
                for tier, ladder in models.TIERS.items()
            ],
            "defaultModelTier": models.DEFAULT_TIER,
            "limits": {
                "maxAgents": int(os.environ.get("MAX_AGENTS", A.DEFAULT_MAX_AGENTS)),
                "maxConcurrentRuns": A.MAX_CONCURRENT_RUNS_CEILING,
                "maxMonthlyUsd": A.MAX_MONTHLY_USD_CEILING,
            },
            "connectors": [
                {"connectorId": c.connector_id,
                 "capability": c.capability.value,
                 "allowedTools": sorted(c.allowed_tools)}
                for c in _org_connectors(store).values()
            ],
        })

    if (p := _match(path, "/agents/{id}")) and method == "GET":
        agent = store.get(K.agent_pk(p[0]), "META")
        agent["memory"] = store.query(K.agent_pk(p[0]), sk_prefix="MEM#")
        agent["grants"] = store.query(K.agent_pk(p[0]), sk_prefix="GRANT#")
        agent["audit"] = store.query(K.agent_pk(p[0]), sk_prefix="AUDIT#",
                                     limit=50, ascending=False)
        # Raw assignment rows, not `skills.assigned_active_skills` -- the
        # profile needs to show a skill pending approval or disabled too,
        # not only what the prompt is currently allowed to see.
        agent["skillAssignments"] = store.query(K.agent_pk(p[0]), sk_prefix="SKILLASSIGN#")
        return _resp(200, agent)

    if (p := _match(path, "/agents/{id}")) and method == "PATCH":
        existing = store.get(K.agent_pk(p[0]), "META")
        changes, events = A.plan_update(existing, body, _actor(event))
        updated = store.update(K.agent_pk(p[0]), "META", changes)
        for ev in events:
            store.put(ev)
        return _resp(200, updated)

    if (p := _match(path, "/agents/{id}")) and method == "DELETE":
        # Deactivation, never deletion. An agent that produced evidence must
        # remain something that evidence can point at.
        existing = store.get(K.agent_pk(p[0]), "META")
        changes, events = A.plan_update(existing, {"status": "archived"},
                                        _actor(event))
        updated = store.update(K.agent_pk(p[0]), "META", changes)
        for ev in events:
            store.put(ev)
        return _resp(200, updated)

    # --- connectors --------------------------------------------------------
    # Ordered before /connectors/{id} so these never resolve as an id.
    if path == "/connectors/catalog" and method == "GET":
        return _resp(200, {"catalog": C.catalog_for_console()})

    if path == "/connectors/apps" and method == "GET":
        qs = event.get("queryStringParameters") or {}
        try:
            limit = int(qs.get("limit", "48"))
        except (TypeError, ValueError):
            return _resp(400, {"error": "invalid_request", "detail": "limit must be an integer"})
        return _resp(200, _pipedream().apps(
            after=qs.get("after"), q=qs.get("q"), limit=limit))

    if path == "/connectors" and method == "GET":
        return _resp(200, {"connectors": list(C.installed(store).values())})

    if path == "/connectors/connect-token" and method == "POST":
        # Mints a short-lived token for Pipedream's own authorization UI. The
        # owner authorizes the third party there; the resulting credential
        # lives on Pipedream's side and is referenced here only by account id.
        actor = _actor(event)
        token = _pipedream().connect_token(actor.user_id)
        store.put(C.connector_event(
            body.get("connectorId") or "pipedream:unknown",
            "connector.authorization_started",
            actor_user_id=actor.user_id,
            detail="connect token issued"))
        return _resp(201, token)

    if path == "/connectors/accounts" and method == "GET":
        actor = _actor(event)
        qs = event.get("queryStringParameters") or {}
        return _resp(200, {"accounts": _pipedream().accounts(
            actor.user_id, app=qs.get("app"))})

    if (p := _match(path, "/connectors/{id}")) and method == "GET":
        row = store.get(K.connector_pk(p[0]), "META")
        row["log"] = store.query(K.connector_pk(p[0]), sk_prefix="LOG#",
                                 limit=50, ascending=False)
        return _resp(200, row)

    if (p := _match(path, "/connectors/{id}/install")) and method == "POST":
        actor = _actor(event)
        account_id = (body.get("accountId") or "").strip()
        if not account_id:
            return _resp(400, {"error": "invalid_request",
                               "detail": "accountId is required "
                                         "(authorize the app first)"})
        row = C.install(store, p[0], account_id=account_id,
                        external_user_id=actor.user_id,
                        actor_user_id=actor.user_id,
                        allowed_tools=body.get("allowedTools"))
        store.put(C.connector_event(p[0], "connector.installed",
                                    actor_user_id=actor.user_id,
                                    detail=f"tools: {row['allowedTools']}"))
        return _resp(201, row)

    if (p := _match(path, "/connectors/{id}")) and method == "DELETE":
        actor = _actor(event)
        store.get(K.connector_pk(p[0]), "META")   # 404 if it is not ours
        result = C.revoke(store, p[0])
        store.put(C.connector_event(
            p[0], "connector.revoked", actor_user_id=actor.user_id,
            detail=f"grants removed from: {result['revokedFrom'] or 'no agents'}"))
        return _resp(200, result)

    # --- memory ------------------------------------------------------------
    if (p := _match(path, "/agents/{id}/memory")) and method == "POST":
        row = _write_memory(store, K.agent_pk(p[0]), body, scope="agent", actor=_actor(event))
        _event_in_dm(store, p[0], f"Saved to memory: {_label(row)}", icon="layers",
                     memId=row["memId"])
        return _resp(201, row)

    if (p := _match(path, "/agents/{id}/memory/{memId}")) and method == "PATCH":
        existing = store.get(K.agent_pk(p[0]), K.memory_sk(p[1]))
        updated = store.update(K.agent_pk(p[0]), K.memory_sk(p[1]), memory.plan_edit(existing, body))
        _event_in_dm(store, p[0], f"Memory corrected: {_label(updated)}", icon="layers",
                     memId=p[1])
        return _resp(200, updated)

    if (p := _match(path, "/agents/{id}/memory/{memId}")) and method == "DELETE":
        store.delete(K.agent_pk(p[0]), K.memory_sk(p[1]))
        return _resp(204, {})

    if (p := _match(path, "/agents/{id}/memory/{memId}/revoke")) and method == "POST":
        return _resp(200, store.update(K.agent_pk(p[0]), K.memory_sk(p[1]), memory.revoke()))

    # --- shared user memory --------------------------------------------
    # Facts every seat should know (name, timezone, standing preferences),
    # not scoped to one agent's namespace. See 16-grokbot-ux-alignment §4.
    # This route is only ever reached with a human Actor (api.py sits behind
    # Auth0; an agent has no bearer token), so a direct POST here always
    # publishes -- an agent can only reach shared memory through
    # `propose_shared_memory` -> the `memory.publish` approval below.
    if path == "/memory" and method == "GET":
        return _resp(200, {"memory": store.query(K.user_pk(store.owner_id), sk_prefix="MEM#", limit=100)})

    if path == "/memory" and method == "POST":
        return _resp(201, _write_memory(store, K.user_pk(store.owner_id), body,
                                        scope="shared_user", actor=_actor(event)))

    if (p := _match(path, "/memory/{memId}")) and method == "PATCH":
        existing = store.get(K.user_pk(store.owner_id), K.memory_sk(p[0]))
        return _resp(200, store.update(K.user_pk(store.owner_id), K.memory_sk(p[0]),
                                       memory.plan_edit(existing, body)))

    if (p := _match(path, "/memory/{memId}")) and method == "DELETE":
        store.delete(K.user_pk(store.owner_id), K.memory_sk(p[0]))
        return _resp(204, {})

    if (p := _match(path, "/memory/{memId}/revoke")) and method == "POST":
        # Excluded from the very next prompt: orchestrator._drive re-reads
        # status on every run and memory.visible() drops anything not
        # `published`, so there is nothing left to invalidate.
        return _resp(200, store.update(K.user_pk(store.owner_id), K.memory_sk(p[0]), memory.revoke()))

    # --- task-scoped memory ----------------------------------------------
    # Visible only to a run that is actually part of this task -- see
    # orchestrator._drive's `effective_task_id` query. Never crosses to a
    # different task because a different task's run never queries this pk.
    if (p := _match(path, "/tasks/{taskId}/memory")) and method == "GET":
        return _resp(200, {"memory": store.query(K.task_pk(p[0]), sk_prefix="MEM#", limit=100)})

    if (p := _match(path, "/tasks/{taskId}/memory")) and method == "POST":
        body = {**body, "taskId": p[0]}
        return _resp(201, _write_memory(store, K.task_pk(p[0]), body, scope="task",
                                        actor=_actor(event)))

    # --- skills --------------------------------------------------------
    if path == "/skills" and method == "GET":
        return _resp(200, {"skills": store.query_index("gsi1", "gsi1pk", "SKILLS", limit=200)})

    if path == "/skills" and method == "POST":
        # A person authoring a skill directly goes straight to active,
        # version 1; only an agent's `propose_skill` call produces a
        # `proposed` row awaiting this same approval gate agent creation
        # uses. Written atomically -- META + V1 or neither.
        source = body.get("sourceThreadId")
        created = skills.create(store, {k: v for k, v in body.items() if k != "sourceThreadId"},
                                created_by=_owner(event))
        # Saved *from* a conversation (the console's "Save as skill" on a message):
        # the history line is written by the save itself, in that conversation.
        if isinstance(source, str) and store.try_get(K.thread_pk(source), "META"):
            threads.event(store, source, f"Saved as a skill: {created['name']}", icon="file")
        return _resp(201, created)

    if (p := _match(path, "/skills/{id}")) and method == "PATCH":
        existing = store.get(K.skill_pk(p[0]), "META")
        if body.get("status") == "active" and existing["status"] == "proposed":
            return _resp(200, store.update(K.skill_pk(p[0]), "META", skills.activate(existing)))
        changes = {k: v for k, v in body.items()
                  if k in {"name", "description", "status", "owner"}}
        if changes.get("status") not in (None, *skills.STATUSES):
            return _resp(400, {"error": f"status must be one of {sorted(skills.STATUSES)}"})
        if not changes:
            return _resp(400, {"error": "no editable fields supplied"})
        return _resp(200, store.update(K.skill_pk(p[0]), "META", changes))

    # --- skill versions --------------------------------------------------
    # Immutable once written. Only `tools`/`capabilities`/`approvalRequired`
    # changing forces the human-approval step below; a body/description-only
    # edit becomes current immediately -- see skills.version_needs_approval.
    if (p := _match(path, "/skills/{id}/versions")) and method == "GET":
        return _resp(200, {"versions": store.query(K.skill_pk(p[0]), sk_prefix="V#", limit=1000)})

    if (p := _match(path, "/skills/{id}/versions")) and method == "POST":
        skill_id = p[0]
        latest = skills.latest_version(store, skill_id)
        fields = skills.validate_skill({**body, "name": store.get(K.skill_pk(skill_id), "META")["name"]})
        next_version = (latest["version"] + 1) if latest else 1
        needs_approval = bool(latest) and skills.version_needs_approval(latest, fields)
        row = skills.propose_version(fields, skill_id=skill_id, next_version=next_version,
                                     created_by=_owner(event))
        if not needs_approval:
            return _resp(201, skills.apply_version(store, skill_id, row, approved_by=_owner(event)))
        store.put(row)
        return _resp(202, {**row, "status": "pending_approval"})

    if (p := _match(path, "/skills/{id}/versions/{version}/approve")) and method == "POST":
        skill_id, version = p[0], int(p[1])
        pending = store.get(K.skill_pk(skill_id), K.skill_version_sk(version))
        return _resp(200, skills.apply_version(store, skill_id, pending, approved_by=_owner(event)))

    # --- skill assignment --------------------------------------------------
    # Assignment, not blanket injection: an active skill an agent is not
    # assigned to never enters that agent's prompt (orchestrator._drive uses
    # skills.assigned_active_skills, not the full SKILLS listing).
    if (p := _match(path, "/skills/{id}/assignments")) and method == "POST":
        skill_id = p[0]
        skill = store.get(K.skill_pk(skill_id), "META")
        version = int(body.get("version") or skill["currentVersion"])
        return _resp(201, skills.assign(store, skill_id=skill_id, agent_id=body["agentId"],
                                        version=version, assigned_by=_owner(event)))

    if (p := _match(path, "/skills/{id}/assignments/{agentId}")) and method == "DELETE":
        skills.unassign(store, skill_id=p[0], agent_id=p[1])
        return _resp(204, {})


    if (p := _match(path, "/skills/{id}")) and method == "DELETE":
        store.delete(K.skill_pk(p[0]), "META")
        return _resp(204, {})

    # --- threads -----------------------------------------------------------
    if path == "/threads" and method == "GET":
        # Not named `threads`: that would make the `threads` module a *local* of
        # this whole function, and every other branch that calls `threads.event`
        # would fail with UnboundLocalError.
        rows = store.query_index("gsi1", "gsi1pk", "THREADS")
        # Derived, not stored: a cached boolean would disagree with
        # lastActivity the moment a run wrote to a thread nobody has opened.
        #
        # Strictly greater, so marking read clears the thread it was called
        # on. Timestamps are second-resolution, which means activity landing
        # in the same second as the marker counts as read -- the alternative,
        # `>=`, makes a read never clear anything.
        for thread in rows:
            marker = store.try_get(K.thread_pk(thread["threadId"]), "READ") or {}
            thread["readAt"] = marker.get("readAt")
            thread["unread"] = bool(
                thread.get("lastActivity")
                and thread["lastActivity"] > (marker.get("readAt") or ""))
        return _resp(200, {"threads": rows})

    if path == "/threads" and method == "POST":
        agent_ids = body.get("agentIds", [])
        if not isinstance(agent_ids, list) or not all(isinstance(a, str) for a in agent_ids):
            raise A.ValidationError("agentIds must be a list of agent ids")
        if len(set(agent_ids)) > collab.MAX_ROOM_MEMBERS:
            raise A.ValidationError(
                f"a room holds at most {collab.MAX_ROOM_MEMBERS} agents")
        thread_id = new_id("th_")
        actor = _actor(event)
        return _resp(201, store.put({
            "pk": K.thread_pk(thread_id), "sk": "META",
            "entity": "Thread", "threadId": thread_id,
            "gsi1pk": "THREADS", "gsi1sk": now_iso(),
            "kind": body.get("kind", "dm"),
            "title": body.get("title", "New task"),
            "agentIds": body.get("agentIds", []),
            "sessionId": K.session_id(thread_id),
            "lastActivity": now_iso(),
            # A room is task-bound from creation: the human who opened it is
            # its owner of record, and it starts "active" so the Rooms list
            # never has to guess a status for a room with no runs yet.
            "createdBy": actor.user_id,
            "status": body.get("status", "active"),
        }))

    if (p := _match(path, "/threads/{id}")) and method == "PATCH":
        return _patch_room(store, p[0], body)

    if (p := _match(path, "/threads/{id}")) and method == "GET":
        thread = store.get(K.thread_pk(p[0]), "META")
        # Agent-to-agent traffic (`AgentMessage`) is a coordination event, not
        # a chat turn -- see /threads/{id}/coordination. Mixing it into
        # `messages` would render it as an ordinary bubble in the room the
        # owner reads, which is exactly the "looks like a shared DM" framing
        # this route must not produce.
        rows = store.query(K.thread_pk(p[0]), sk_prefix="MSG#", limit=200)
        thread["messages"] = [r for r in rows if r.get("entity") != "AgentMessage"]
        return _resp(200, thread)

    if (p := _match(path, "/threads/{id}/messages")) and method == "POST":
        return _post_message(store, p[0], body)

    if (p := _match(path, "/threads/{id}/exec")) and method == "POST":
        return _exec(store, p[0], body)

    # --- coordination (read-only) -------------------------------------
    # The owner's view onto agent<->agent traffic bound to this thread:
    # handoffs proposed on any run this thread has driven, plus the
    # AgentMessage rows `collab.send` wrote here (room or task context --
    # both land on this same thread pk; see collab._context_pk). Never a
    # write path: sender/recipient are the only agents who may address one
    # another here, and the owner is a reader, not a third participant.
    if (p := _match(path, "/threads/{id}/coordination")) and method == "GET":
        store.get(K.thread_pk(p[0]), "META")   # 404 if the thread is not ours
        agent_messages = [r for r in store.query(K.thread_pk(p[0]), sk_prefix="MSG#", limit=200)
                          if r.get("entity") == "AgentMessage"]
        thread_runs = [r for r in store.query_index("gsi1", "gsi1pk", "RUNS", limit=500)
                      if r.get("threadId") == p[0]]
        handoffs = [(r, h) for r in thread_runs
                   for h in store.query(r["pk"], sk_prefix="HOFF#", limit=200)]
        items = [
            *({"kind": "message", "at": m.get("at") or m.get("createdAt"),
               "fromAgentId": m.get("senderAgentId"), "toAgentId": m.get("recipientAgentId"),
               "status": "delivered", "priority": _priority_label(m),
               "summary": m.get("text", "")[:200],
               "taskId": m.get("taskId"), "collaborationContextId": m.get("collaborationContextId"),
              } for m in agent_messages),
            *({"kind": "handoff", "at": h.get("createdAt"),
               "fromAgentId": h.get("fromAgentId"), "toAgentId": h.get("toAgentId"),
               "status": h.get("status", "proposed"), "priority": None,
               "summary": h.get("goal", ""), "taskId": r["runId"],
               "collaborationContextId": None,
              } for r, h in handoffs),
        ]
        items.sort(key=lambda i: i.get("at") or "")
        return _resp(200, {"coordination": items})

    # --- runs --------------------------------------------------------------
    if (p := _match(path, "/runs/{id}")) and method == "GET":
        run = store.get(K.run_pk(p[0]), "META")
        run["approvals"] = [approvals.to_card(a) for a in approvals.for_run(store, run["pk"])]
        run["events"] = store.query(run["pk"], sk_prefix="EVT#", limit=200)
        return _resp(200, run)

    if (p := _match(path, "/runs/{id}/cancel")) and method == "POST":
        run = store.get(K.run_pk(p[0]), "META")
        if RunState(run["state"]) in TERMINAL:
            return _resp(409, {"error": "run already finished", "state": run["state"]})
        return _resp(202, _stop_run(store, run))

    # --- approvals ---------------------------------------------------------
    # Global, across every run -- what the Home inbox needs. The per-run
    # route above stays the source of truth for one run's own approvals;
    # this is a read-only index scan of the same rows, never a second
    # decision path.
    if path == "/approvals" and method == "GET":
        qs = event.get("queryStringParameters") or {}
        wanted = qs.get("status", approvals.PENDING)
        rows = store.query_index("gsi1", "gsi1pk", "APPROVALS", limit=500)
        matched = [a for a in rows if a.get("status") == wanted] if wanted else rows
        return _resp(200, {"approvals": [approvals.to_card(a) for a in matched]})

    if (p := _match(path, "/approvals/{runId}/{apvId}")) and method == "POST":
        return _decide(store, p[0], p[1], body, _actor(event))

    # --- read state --------------------------------------------------------
    # The inbox orders on lastActivity and has never had anything to compare
    # it against, so every conversation looked equally attended to. A marker
    # per thread is the whole feature: unread is `lastActivity > readAt`,
    # derived on read rather than stored, so it cannot drift from the
    # activity it describes.
    if (p := _match(path, "/threads/{id}/read")) and method == "POST":
        thread = store.get(K.thread_pk(p[0]), "META")   # 404 if not ours
        at = body.get("at") or thread.get("lastActivity") or now_iso()
        marker = store.put({
            "pk": K.thread_pk(p[0]), "sk": "READ",
            "entity": "ReadMarker", "threadId": p[0], "readAt": at,
        })
        return _resp(200, {"threadId": p[0], "readAt": marker["readAt"]})

    # --- routines ----------------------------------------------------------
    # The execution half of routines has existed since the first build:
    # `handlers/routine.py` claims the fire, opens or reuses a thread, and
    # starts a run. There was simply no way to create one. These routes write
    # exactly the record that handler already reads -- agentId, prompt,
    # enabled, trigger, limits, threadId -- so nothing here invents a shape
    # the worker would not understand.
    if path == "/routines" and method == "GET":
        return _resp(200, {"routines": store.query_index("gsi1", "gsi1pk", "ROUTINES")})

    if path == "/routines" and method == "POST":
        try:
            record = R.plan_create(body, _actor(event))
        except R.ValidationError as exc:
            return _resp(400, {"error": "invalid_request", "detail": str(exc)})
        agent = store.get(K.agent_pk(record["agentId"]), "META")   # 404 for a foreign agent
        # The agent's zone decides when "every weekday at 9" is.
        record["timezone"] = agent.get("timezone")
        written = store.put(record)
        try:
            _schedule(store, written)
        except Exception as exc:  # noqa: BLE001
            # A routine whose schedule was refused would sit in the list
            # looking armed and never fire. Leave nothing behind instead.
            traceback.print_exc()
            store.delete(K.routine_pk(written["routineId"]), "META")
            return _resp(502, {
                "error": "schedule_failed",
                "detail": f"{type(exc).__name__}: {exc}",
                "note": "nothing was left behind; the same request can be retried",
            })
        # Written by the action itself, after it succeeded -- so the transcript
        # never claims a routine that a refused schedule did not leave behind.
        _event_in_dm(store, written["agentId"],
                     f"Routine created: {written['name']} · {R.describe_schedule(written.get('trigger'))}",
                     icon="clock", routineId=written["routineId"])
        return _resp(201, written)

    # Run now. The same `routines.fire` a scheduled fire uses -- same claim, same
    # thread, same limits -- so it cannot behave differently for having been
    # pressed by a person. A paused routine may still be run by hand: pausing
    # stops the schedule, not the operator.
    if (p := _match(path, "/routines/{id}/run")) and method == "POST":
        routine = store.get(K.routine_pk(p[0]), "META")
        if routine.get("status") == "archived":
            return _resp(409, {"error": "conflict", "detail": "an archived routine cannot be run"})
        key = _header(event, "idempotency-key") or now_iso()
        result = R.fire(store, routine,
                        invoke=lambda run_id: _invoke_orchestrator(run_id, store.owner_id),
                        trigger_type="manual",
                        idempotency_key=f"manual:{routine['routineId']}:{key}")
        return _resp(202, result)

    if (p := _match(path, "/routines/{id}")) and method == "GET":
        return _resp(200, store.get(K.routine_pk(p[0]), "META"))

    if (p := _match(path, "/routines/{id}")) and method == "PATCH":
        existing = store.get(K.routine_pk(p[0]), "META")
        try:
            changes = R.plan_update(existing, body)
        except R.ValidationError as exc:
            return _resp(400, {"error": "invalid_request", "detail": str(exc)})
        updated = store.update(K.routine_pk(p[0]), "META", changes)
        _schedule(store, updated)
        return _resp(200, updated)

    if (p := _match(path, "/routines/{id}")) and method == "DELETE":
        # Disabled, not deleted. A routine that has fired owns runs and
        # evidence, and those must keep pointing at something -- the same
        # reason an agent is archived rather than removed.
        store.get(K.routine_pk(p[0]), "META")
        archived = store.update(K.routine_pk(p[0]), "META",
                                {"enabled": False, "status": "archived"})
        # The record survives; the schedule must not, or a routine the owner
        # switched off keeps firing.
        _schedule(store, archived)
        return _resp(200, archived)

    # --- artifacts ---------------------------------------------------------
    # A sealed evidence bundle, listed. The manifest lives in S3 and the run
    # holds the pointer, so this is a projection of runs that produced one --
    # never a second copy of the bundle, which is append-only and must have
    # exactly one home.
    if path == "/artifacts" and method == "GET":
        rows = store.query_index("gsi1", "gsi1pk", "RUNS", limit=500)
        sealed = [r for r in rows if r.get("evidenceKey")]
        sealed.sort(key=lambda r: r.get("endedAt") or r.get("startedAt") or "",
                    reverse=True)
        return _resp(200, {"artifacts": [{
            "runId": r["runId"], "agentId": r.get("agentId"),
            "threadId": r.get("threadId"), "goal": r.get("goal"),
            "outcome": r.get("state"), "summary": r.get("summary"),
            "evidenceKey": r.get("evidenceKey"),
            "startedAt": r.get("startedAt"), "endedAt": r.get("endedAt"),
            "costUsd": r.get("costUsd"),
        } for r in sealed]})

    # --- settings ----------------------------------------------------------
    # Owner preferences. One row, defaulted on read rather than seeded on
    # sign-up, so an account that predates this route answers with the
    # defaults instead of a 404.
    if path == "/settings" and method == "GET":
        return _resp(200, S.read(store))

    if path == "/settings" and method == "PUT":
        try:
            return _resp(200, S.write(store, body))
        except S.ValidationError as exc:
            return _resp(400, {"error": "invalid_request", "detail": str(exc)})

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


def _write_memory(store: Store, pk: str, body: dict, *, scope: str, actor: A.Actor) -> dict:
    """Write one published memory row, tagged by scope and kind.

    Every route that reaches this helper runs behind Auth0 (`api.py` never
    sees an agent-originated request -- see `_actor`), so a `shared_user`
    write here is always human-authored and always publishes immediately.
    An agent can only reach `shared_user` through `propose_shared_memory` ->
    the `memory.publish` approval in `_decide`.

    `kind` supersedes the old bare `pinned` flag: `foundational` always
    enters context (what `pinned: true` used to mean), `log` is dated
    history, `note` is short-lived. A caller that still only sends `pinned`
    keeps working exactly as before.
    """
    pinned = bool(body.get("pinned", True))
    kind = body.get("kind") or ("foundational" if pinned else "note")
    row = memory.plan_write({**body, "kind": kind}, pk, scope=scope,
                            source="user", author=actor.user_id, status="published")
    return store.put(row)


def _create_agent(store: Store, body: dict, event: dict):
    """Create one agent, atomically, or leave nothing behind.

    Two phases, because one of the steps is not a database write:

    1. Every row the agent consists of — identity, grants, memory namespace,
       starter thread, audit entry — goes in as a single transaction, with the
       agent marked `provisioning`. Nothing hands work to a `provisioning`
       agent, so a crash after this point leaves something inert, not
       something half-empowered.
    2. The harness is created. Only on success does the agent become
       `active`. On failure the transaction is rolled back, so the caller can
       retry the same request rather than clean up after it.

    The audit trail of a failed attempt is written after the rollback and
    deliberately survives it: "this creation was tried and failed" is a fact
    worth keeping, and it is the only trace that would otherwise be lost.
    """
    actor = _actor(event)

    # Idempotency first: a retried create must return the first agent, not a
    # second one with a suffixed id.
    idem_key = _header(event, "idempotency-key")
    if idem_key:
        existing_id = store.claim(f"agent:{idem_key}", "pending", field="agentId")
        if existing_id and existing_id != "pending":
            agent = store.try_get(K.agent_pk(existing_id), "META")
            if agent:
                return _resp(200, agent)

    active = [r for r in store.query_index("gsi1", "gsi1pk", "AGENTS", limit=200)
              if r.get("status", r.get("state")) in A.SEATED]

    plan = A.plan_create(
        body, actor,
        org_connectors=_org_connectors(store),
        active_count=len(active),
        max_agents=int(os.environ.get("MAX_AGENTS", A.DEFAULT_MAX_AGENTS)),
        has_entrypoint=any(r.get("entrypoint") for r in active),
    )

    if store.try_get(K.agent_pk(plan.agent_id), "META"):
        return _resp(409, {"error": "conflict",
                           "detail": f"agent {plan.agent_id!r} already exists"})

    store.transact_put(plan.items)
    if idem_key:
        store.update(K.idempotency_pk(f"agent:{idem_key}"), "META",
                     {"agentId": plan.agent_id})

    try:
        provisioned = _provision_harness(store, plan.agent)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        store.transact_delete(plan.rollback_keys)
        store.put(A.audit_event(plan.agent_id, "agent.provision_failed", actor,
                                detail=f"{type(exc).__name__}: {exc}"))
        return _resp(502, {
            "error": "provisioning_failed",
            "detail": f"{type(exc).__name__}: {exc}",
            "agentId": plan.agent_id,
            "note": "nothing was left behind; the same request can be retried",
        })

    return _resp(201, provisioned)


def _schedule(store: Store, routine: dict) -> None:
    """Put the routine's EventBridge schedule in step with its record.

    Separated for the same reason `_provision_harness` is: the whole routine
    path can then be exercised without an AWS account, while the one call
    that reaches a service stays in `amazai.schedules`.
    """
    schedules.put(routine, store.owner_id)


def _provision_harness(store: Store, agent: dict) -> dict:
    """Give the agent its runtime identity and mark it runnable.

    Separated so the whole create path can be exercised without an AWS
    account: a test swaps this for a stub and still drives the transaction,
    the rollback and the audit trail.
    """
    model_id = (agent.get("model") or {}).get("modelId")
    if not model_id:
        # An unresolved model is the intended failure, not a surprise. A
        # guessed Bedrock identifier fails later, in a way that reads as a
        # permissions bug (decision D2).
        raise RuntimeError(
            f"no modelId resolved for tier "
            f"{(agent.get('model') or {}).get('tier')!r}; "
            "run scripts/resolve_models.py against this account first"
        )

    role_arn = os.environ.get("AGENT_ROLE_ARN_TEMPLATE", "").format(
        agentId=agent["agentId"]) or None
    client = agentcore.AgentCore()
    harness_arn = client.create_harness(
        name=f"amazai_{agent['agentId']}",
        execution_role_arn=role_arn,
        tool_names=agent.get("allowedTools") or [],
    )

    return store.update(K.agent_pk(agent["agentId"]), "META", {
        "harnessArn": harness_arn,
        "executionRoleArn": role_arn,
        "status": "active",
        "state": "active",
    })


def _priority_label(agent_message: dict) -> str:
    """`collab.send`'s two outcomes for `priority: true`, in the words the
    console must show -- see docs/architecture/17 §1: a priority request
    only ever asks for an expedited wake, so a request that did not clear
    the rate/concurrency/budget gate is still delivered, just not now."""
    if agent_message.get("priorityGranted"):
        return "Priority — recipient awakened"
    if agent_message.get("priorityRequested"):
        return "Deferred — delivered on next turn"
    return "Deferred — delivered on next turn"


def _post_message(store: Store, thread_id: str, body: dict):
    """A message from the operator, and every run it starts.

    Three things beyond "save it and start a run":

    * **Who wakes.** A room wakes every Bot it `@`-mentions, each on its own run,
      in parallel (`dispatch.targets_for`); a direct thread wakes its one Bot.
    * **`/skill`.** A leading `/name` that names a skill *this Bot has* -- active
      and assigned -- rides on the run so the Bot is told to use it. A `/name`
      that names nothing is ordinary text, never an error.
    * **Redirect.** `redirectRunId` names a run to stop in favour of this message.
      It is stopped, not raced: a new run does not start beside the old one on
      the same session, it starts when the old one has actually ended.
    """
    text = (body.get("text") or "").strip()
    if not text:
        return _resp(400, {"error": "text is required"})

    thread = store.get(K.thread_pk(thread_id), "META")
    explicit = body.get("agentId")
    targets = [explicit] if explicit else dispatch.targets_for(thread, text)
    if not targets:
        return _resp(400, {"error": "no agent assigned to this thread"})

    # A mention in a direct thread is an ask to hand off, not a wake -- the Bot
    # is told who was named and the handoff machinery decides what happens.
    mentions: list[str] = []
    if thread.get("kind") != "room" and "@" in text:
        active = [r["agentId"] for r in store.query_index("gsi1", "gsi1pk", "AGENTS", limit=200)
                  if r.get("status", r.get("state")) in A.SEATED and r["agentId"] != targets[0]]
        mentions = dispatch.mentioned(active, text)

    skill = None
    command = dispatch.slash_command(text)
    if command and len(targets) == 1:
        wanted = command[0].lower()
        for row in skills.assigned_active_skills(store, targets[0]):
            if wanted in (row["skillId"].lower(), skills.normalize_skill_id(row["name"])):
                skill = {"skillId": row["skillId"], "name": row["name"]}
                break

    store.put({
        "pk": K.thread_pk(thread_id), "sk": K.message_sk(now_iso(), ordered_suffix()),
        "entity": "Message", "role": "user", "author": "you", "text": text,
        **({"skill": skill["name"]} if skill else {}),
    })
    store.update(K.thread_pk(thread_id), "META", threads.touch(text, "user"))

    names = {}
    if len(targets) > 1:
        for a in targets:
            row = store.try_get(K.agent_pk(a), "META")
            names[a] = (row or {}).get("name", a)
        threads.event(store, thread_id, "Woke " + " and ".join(names[a] for a in targets)
                      if len(targets) == 2 else "Woke " + ", ".join(names[a] for a in targets),
                      icon="check")

    redirect = None
    if body.get("redirectRunId"):
        old = store.try_get(K.run_pk(body["redirectRunId"]), "META")
        if (old and old.get("threadId") == thread_id and old.get("agentId") in targets
                and RunState(old["state"]) not in TERMINAL
                and RunState(old["state"]) is not RunState.CANCELLING):
            redirect = old

    started = []
    for agent_id in targets:
        if redirect and redirect["agentId"] == agent_id:
            _stop_run(store, redirect, redirect_text=text)
            started.append({"runId": redirect["runId"], "agentId": agent_id,
                            "state": RunState.CANCELLING.value, "redirected": True})
            continue
        trigger = {"type": "user"}
        if skill:
            trigger["skill"] = skill
        if mentions:
            trigger["mentions"] = mentions
        if len(targets) > 1:
            trigger["woke"] = [names[a] for a in targets]
            trigger["mentions"] = targets
        run = runs.create(store, agent_id=agent_id, thread_id=thread_id, goal=text,
                          trigger=trigger)
        _invoke_orchestrator(run["runId"], store.owner_id)
        started.append({"runId": run["runId"], "agentId": agent_id, "state": run["state"]})

    return _resp(202, {"runId": started[0]["runId"], "state": started[0]["state"],
                       "runs": started})


def _stop_run(store: Store, run: dict, *, redirect_text: str | None = None) -> dict:
    """Stop a run, and optionally record what to do once it has stopped.

    A running run is stopped by flagging it: the orchestrator sees the flag
    between stream events and settles it. A *paused* run has no orchestrator
    watching -- nothing is held open while it waits -- so it is settled by
    invoking the orchestrator to do exactly that. Before this, stopping a run
    that was waiting on an approval flagged it `CANCELLING` and left it there
    for good: the one state a stop most needs to work in was the one where it
    silently did not.
    """
    state = RunState(run["state"])
    if state in TERMINAL or state is RunState.CANCELLING:
        return run
    if redirect_text:
        store.update(run["pk"], "META", {"redirect": {"text": redirect_text, "at": now_iso()}})
    stopped = runs.advance(store, run, RunState.CANCELLING)
    if state in PAUSED:
        pending = (run.get("pending") or {}).get("approvalId")
        if pending:
            try:   # what it was waiting on is denied, not left dangling
                approvals.decide(store, run["pk"], pending, approve=False,
                                 note="the run was stopped")
            except Exception:  # noqa: BLE001 -- already decided or expired: fine
                pass
        _invoke_orchestrator(run["runId"], store.owner_id, cancel=True)
    return stopped


def _label(row: dict) -> str:
    return (row.get("title") or row.get("body") or "").strip()[:80] or "an entry"


def _event_in_dm(store: Store, agent_id: str, text: str, *, icon: str = "check", **extra) -> None:
    """A history line in an agent's direct thread -- if it has one. Best effort:
    the action it records has already happened, and a missing thread must not
    turn a success into an error."""
    thread_id = f"dm-{agent_id}"
    try:
        if store.try_get(K.thread_pk(thread_id), "META"):
            threads.event(store, thread_id, text, icon=icon, **extra)
    except Exception:  # noqa: BLE001
        traceback.print_exc()


def _patch_room(store: Store, thread_id: str, body: dict):
    """Change a room's title or who is in it.

    The cap (`collab.MAX_ROOM_MEMBERS`) and the "must be a real, seated agent"
    check are the same ones creating a room applies -- membership is not a way
    round either.
    """
    thread = store.get(K.thread_pk(thread_id), "META")
    if thread.get("kind") != "room":
        return _resp(400, {"error": "invalid_request",
                           "detail": "only a room's members can be changed"})
    if thread.get("readOnly") or thread.get("status") == "completed":
        return _resp(409, {"error": "conflict", "detail": "this room has finished"})

    changes: dict = {}
    if "title" in body:
        title = (body.get("title") or "").strip()
        if not 2 <= len(title) <= 80:
            raise A.ValidationError("title must be 2-80 characters")
        changes["title"] = title

    added: list[str] = []
    removed: list[str] = []
    if "agentIds" in body:
        ids = body["agentIds"]
        if not isinstance(ids, list) or not all(isinstance(a, str) for a in ids):
            raise A.ValidationError("agentIds must be a list of agent ids")
        ids = list(dict.fromkeys(ids))
        if not ids:
            raise A.ValidationError("a room needs at least one agent")
        if len(ids) > collab.MAX_ROOM_MEMBERS:
            raise A.ValidationError(f"a room holds at most {collab.MAX_ROOM_MEMBERS} agents")
        for agent_id in ids:
            row = store.try_get(K.agent_pk(agent_id), "META")
            if not row or row.get("status", row.get("state")) not in A.SEATED:
                raise A.ValidationError(f"no such agent {agent_id!r}")
        before = thread.get("agentIds") or []
        added = [a for a in ids if a not in before]
        removed = [a for a in before if a not in ids]
        changes["agentIds"] = ids

    if not changes:
        raise A.ValidationError("no editable fields supplied")

    updated = store.update(K.thread_pk(thread_id), "META", changes)

    def name(agent_id: str) -> str:
        return (store.try_get(K.agent_pk(agent_id), "META") or {}).get("name", agent_id)

    for agent_id in added:
        threads.event(store, thread_id, f"{name(agent_id)} joined", icon="check")
    for agent_id in removed:
        threads.event(store, thread_id, f"{name(agent_id)} left", icon="check")
    return _resp(200, updated)


def _pick_agent(thread: dict, text: str) -> str | None:
    """An @mention wins in a room; otherwise the thread's first agent.

    Kept for callers that want exactly one agent; `_post_message` uses
    `dispatch.targets_for`, which can wake several.
    """
    targets = dispatch.targets_for(thread, text)
    return targets[0] if targets else None


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


def _decide(store: Store, run_id: str, approval_id: str, body: dict,
            actor: A.Actor):
    run_pk = K.run_pk(run_id)
    run = store.get(run_pk, "META")

    if run["state"] != RunState.AWAITING_APPROVAL.value:
        return _resp(409, {"error": "run is not waiting on an approval",
                           "state": run["state"]})

    approve = bool(body.get("approve"))
    decided = approvals.decide(store, run_pk, approval_id,
                               approve=approve, note=body.get("note"))

    created = None
    if approve and decided["action"] == "agent.create":
        created = _create_approved_agent(store, decided["arguments"], actor)
        store.update(run_pk, K.approval_sk(approval_id), {
            "createdAgentId": created["agentId"],
            "executionStatus": "created",
        })
    if approve and decided["action"] == "skill.create":
        created = _create_approved_skill(store, decided["arguments"], actor)
        store.update(run_pk, K.approval_sk(approval_id), {
            "createdSkillId": created["skillId"],
            "executionStatus": "created",
        })
    if approve and decided["action"] == "memory.publish":
        created = _create_approved_memory(store, decided["arguments"], actor)
        store.update(run_pk, K.approval_sk(approval_id), {
            "createdMemId": created["memId"],
            "executionStatus": "created",
        })

    verb = "approved" if approve else "denied"
    note = f'Your decision on "{decided["action"]}": {verb}.'
    if body.get("note"):
        note += f" Note: {body['note']}"
    if not approve:
        note += " Do not attempt this action again; find another way or stop and explain."

    if created:
        # Written by the approval that did it: the transcript only ever says a
        # Bot was created, a skill saved or a fact shared after it happened.
        if decided["action"] == "agent.create":
            threads.event(store, decided["threadId"], f"Created {created['name']}", icon="check")
        elif decided["action"] == "skill.create":
            threads.event(store, decided["threadId"], f"Saved as a skill: {created['name']}", icon="file")
        elif decided["action"] == "memory.publish":
            threads.event(store, decided["threadId"], f"Shared with every Bot: {_label(created)}", icon="layers")

    runs.advance(store, run, RunState.EXECUTING, pending=None)
    _invoke_orchestrator(run_id, store.owner_id, resume=True, resume_note=note)
    result = {"approval": approvals.to_card(decided), "resumed": True}
    if created and decided["action"] == "agent.create":
        result["createdAgent"] = created
    elif created and decided["action"] == "skill.create":
        result["createdSkill"] = created
    elif created and decided["action"] == "memory.publish":
        result["createdMemory"] = created
    return _resp(200, result)


def _create_approved_memory(store: Store, proposal: dict, actor: A.Actor) -> dict:
    """Publish the exact approved shared-memory proposal.

    `proposal` came from the approval's argument binding, not a fresh
    payload -- the same substitution-gap closing `_create_approved_agent`
    and `_create_approved_skill` already rely on. This is the only path by
    which an agent-authored fact ever reaches `shared_user`.
    """
    row = memory.plan_write(proposal, K.user_pk(store.owner_id), scope="shared_user",
                            source="agent", author=proposal.get("proposedBy", ""),
                            status="published")
    return store.put(row)


def _create_approved_skill(store: Store, proposal: dict, actor: A.Actor) -> dict:
    """Create the exact approved skill proposal, atomically (META + v1).

    Mirrors `_create_approved_agent`: the proposal came from the approval's
    argument binding, not a fresh payload, so an agent cannot widen what it
    proposed between the request and the decision.
    """
    meta, version = skills.plan_create(proposal, created_by=actor.user_id,
                                       proposed_by=proposal.get("proposedBy"))
    meta["status"] = "active"
    version["approvedBy"] = actor.user_id
    written = store.transact_put([meta, version])
    return written[0]


def _create_approved_agent(store: Store, proposal: dict, actor: A.Actor) -> dict:
    """Create the exact approved child proposal under the approving owner.

    ``proposal`` comes from the approval's argument binding, never a new
    browser payload. It was normalized in the orchestrator, so it carries no
    grants or optional tools. This closes the approval-to-execution
    substitution gap for agent creation as well as connector actions.
    """
    active = [row for row in store.query_index("gsi1", "gsi1pk", "AGENTS", limit=200)
              if row.get("status", row.get("state")) in A.SEATED]
    plan = A.plan_create(
        proposal, actor,
        org_connectors=_org_connectors(store),
        active_count=len(active),
        max_agents=int(os.environ.get("MAX_AGENTS", A.DEFAULT_MAX_AGENTS)),
    )
    if store.try_get(K.agent_pk(plan.agent_id), "META"):
        raise Conflict(f"agent {plan.agent_id!r} already exists")

    store.transact_put(plan.items)
    try:
        return _provision_harness(store, plan.agent)
    except Exception:
        store.transact_delete(plan.rollback_keys)
        store.put(A.audit_event(plan.agent_id, "agent.provision_failed", actor,
                                detail="approved agent creation could not provision"))
        raise


def _invoke_orchestrator(run_id: str, owner_id: str, *, resume: bool = False,
                         resume_note: str = "", cancel: bool = False) -> None:
    fn = os.environ.get("ORCHESTRATOR_FN_ARN")
    if not fn:
        return
    payload = {"runId": run_id, "ownerId": owner_id}
    if resume:
        payload.update({"resume": True, "resumeNote": resume_note})
    if cancel:
        # Settle a paused run that was stopped: nothing is watching it.
        payload["cancel"] = True
    boto3.client("lambda").invoke(
        FunctionName=fn, InvocationType="Event",
        Payload=json.dumps(payload).encode(),
    )
