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
                    identity, keys as K, memory, models, pipedream, runs, skills)
from amazai.policy import Capability
from amazai.states import RunState
from amazai.store import Conflict, NotFound, Store, new_id, now_iso, ordered_suffix

CORS = {
    "content-type": "application/json",
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "authorization,content-type",
    "access-control-allow-methods": "GET,POST,PATCH,DELETE,OPTIONS",
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
        return _resp(201, _write_memory(store, K.agent_pk(p[0]), body, scope="agent",
                                        actor=_actor(event)))

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
        return _resp(201, skills.create(store, body, created_by=_owner(event)))

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
        return _decide(store, p[0], p[1], body, _actor(event))

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


def _post_message(store: Store, thread_id: str, body: dict):
    text = (body.get("text") or "").strip()
    if not text:
        return _resp(400, {"error": "text is required"})

    thread = store.get(K.thread_pk(thread_id), "META")
    agent_id = body.get("agentId") or _pick_agent(thread, text)
    if not agent_id:
        return _resp(400, {"error": "no agent assigned to this thread"})

    store.put({
        "pk": K.thread_pk(thread_id), "sk": K.message_sk(now_iso(), ordered_suffix()),
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
