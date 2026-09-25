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
import urllib.parse

import boto3

from amazai import (agentcore, agents as A, approvals, artifacts as AR, billing, collab,
                    composio, connectors as C, handoffs, identity, keys as K, memory, models,
                    onboarding, routines as R, runs, schedules, secrets, settings as S,
                    skills, standard_runtime, stripe_client, threads)
from amazai import dispatch, directory as D, govern, org, provisioning
from amazai.policy import Capability
from amazai.states import PAUSED, RunState, TERMINAL
from amazai.store import Conflict, NotFound, Store, new_id, now_iso, ordered_suffix

CORS = {
    "content-type": "application/json",
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "authorization,content-type",
    "access-control-allow-methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
}


def _artifact_s3():
    """The one bucket that holds immutable Bot-produced files.

    Audit manifests live beside these objects, but they are deliberately not
    library entries. A file library should contain things a person can open,
    not a chronological mirror of every conversation or completed run.
    """
    return boto3.client("s3")


def _download_url(client, row: dict) -> str | None:
    bucket = os.environ.get("EVIDENCE_BUCKET", "").strip()
    if not bucket or row.get("storageType") != "s3" or not row.get("storageKey"):
        return None
    return client.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": row["storageKey"]}, ExpiresIn=300)


def _artifact_card(client, row: dict) -> dict:
    # The frontend's contract, unchanged since before this row existed
    # (`Sections.jsx`, `WorkspaceSheet.jsx`) -- `artifactId` used to be the raw
    # S3 key; it is now a real id, and nothing that reads this shape needed
    # to know the difference.
    return {
        "artifactId": row["artifactId"],
        "runId": row.get("runId"),
        "agentId": row.get("createdByAgentId"),
        "name": row.get("name", ""),
        "sizeBytes": row.get("sizeBytes", 0),
        "updatedAt": row.get("updatedAt") or row.get("createdAt"),
        "downloadUrl": _download_url(client, row),
    }


def _artifacts(store: Store, *, status: str | None = None, run_id: str | None = None,
               artifact_type: str | None = None) -> list[dict]:
    """A single indexed query, not the full-bucket scan this route used to
    do (`gsi1pk=ARTIFACTS`, one page). `run_id` narrows to `gsi2` instead --
    a run's own output list -- when the caller asks for one run specifically.
    """
    client = _artifact_s3()
    if run_id:
        rows = AR.list_for_run(store, run_id)
    else:
        rows = AR.list_for_owner(store, artifact_type=artifact_type)
    if status:
        rows = [r for r in rows if r.get("status") == status]
    else:
        rows = [r for r in rows if r.get("status") != "deleted"]
    rows.sort(key=lambda r: str(r.get("updatedAt") or r.get("createdAt") or ""), reverse=True)
    return [_artifact_card(client, r) for r in rows]


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

    This function establishes WHO the caller is (token signature/iss/aud/exp);
    it deliberately does NOT decide whether they may act. The owner allowlist
    (`identity.assert_owner`) is a coarse "is this a workspace owner" gate that
    is applied per-route in `handler()` -- see `_authorize`. Keeping the two
    apart is what lets the finer-grained RBAC matrix govern `/admin/*` for a
    non-owner Admin/Security/Billing/Auditor seat instead of the allowlist
    rejecting them at 401 before RBAC ever runs.
    """
    principal = event.get("_amazai_principal")
    if principal is None:
        principal = identity.principal_from_event(event)
        event["_amazai_principal"] = principal
    return principal


#: Routes that authorize via the RBAC capability matrix (directory.assert_can)
#: rather than the owner allowlist. An invited Admin/Security/Billing/Auditor
#: is by definition NOT an allowlisted owner, so gating these on the allowlist
#: would reject every non-owner seat at 401 before RBAC could make its finer
#: decision -- the two gates would encode conflicting models of who may act.
#: The token is still fully verified for these routes; only the coarse
#: owner-allowlist check is deferred to the per-capability gate inside each
#: /admin/* handler.
_RBAC_ROUTE_PREFIX = "/admin/"


def _authorize(event, path: str) -> identity.Principal:
    """Establish the caller and apply the coarse owner gate where it governs.

    Every route verifies the token via `_principal`. For non-`/admin/*` routes
    the owner allowlist is the authorization model (a private single-owner
    workspace), so `assert_owner` runs here. For `/admin/*` routes the RBAC
    matrix is the model, so the allowlist is skipped and each admin handler
    gates on the specific capability instead. Neither path weakens token
    verification or the Store's ownerId isolation.
    """
    principal = _principal(event)
    if not (path == "/admin" or path.startswith(_RBAC_ROUTE_PREFIX)):
        identity.assert_owner(principal)
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


_composio_client = None


def _composio():
    """One client per container. Built on first use so a request that never
    touches a connector never reads the secret."""
    global _composio_client
    if _composio_client is None:
        _composio_client = composio.Composio()
    return _composio_client


#: Where a person may be sent back to after signing in to an app. The same
#: origins the gateway's CORS allows; anything else gets no callback at all.
_CONSOLE_ORIGINS = ("https://amazai.co", "http://localhost:5173", "http://localhost:4173")


def _return_url(event, slug: str) -> str | None:
    origin = ((event.get("headers") or {}).get("origin") or "").rstrip("/")
    return f"{origin}/marketplace?connected={slug}" if origin in _CONSOLE_ORIGINS else None


def _billing_origin(event) -> str | None:
    """The console origin a Stripe checkout/portal session should return to.

    Read from the request's own Origin header and checked against the same
    allowlist `_return_url` uses, never taken from the request body -- a
    client-supplied success/cancel/return URL would be an open redirect
    through Stripe's own domain.
    """
    origin = ((event.get("headers") or {}).get("origin") or "").rstrip("/")
    return origin if origin in _CONSOLE_ORIGINS else None


def _org_connectors(store: Store) -> dict[str, A.OrgConnector]:
    """The organization's installed connectors: the ceiling for every per-agent grant."""
    return provisioning.org_connectors(store)


def handler(event, context):
    if "provisionOwner" in event:
        # An async self-invoke from `_warm_account_harness`, never an API
        # Gateway event -- those always carry `rawPath`/`requestContext`.
        return _provision_owner_harness(event["provisionOwner"])

    method = (event.get("requestContext", {}).get("http", {}).get("method")
              or event.get("httpMethod") or "GET").upper()
    path = (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"
    if method == "OPTIONS":
        return _resp(204, {})

    if path == "/billing/webhook" and method == "POST":
        # Stripe, never Auth0: no bearer token, no per-owner Store until the
        # verified event itself names one. Handled here, before the JSON
        # parse below, because signature verification needs the exact raw
        # bytes Stripe signed -- a parse-then-reserialize round trip is not
        # guaranteed to reproduce them byte for byte.
        return _billing_webhook(event)

    try:
        body = json.loads(event.get("body") or "{}")
    except ValueError:
        return _resp(400, {"error": "invalid JSON body"})

    try:
        principal = _authorize(event, path)
        store = Store(principal.user_id)
        _, is_new_signup = identity.ensure_user(store, principal)
        if is_new_signup:
            _warm_account_harness(context, store.owner_id)
        # Unconditional and idempotent, exactly like ensure_user above: a
        # brand-new signup gets the free trial grant on this very call, and an
        # account that predates this feature is backfilled with one rather
        # than left permanently without a billing row.
        billing.ensure_billing_row(store)
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
    except (C.NotInstalled, C.NotGranted) as exc:
        return _resp(403, {"error": "forbidden", "detail": str(exc)})
    except C.UnknownConnector as exc:
        return _resp(404, {"error": "not_found", "detail": str(exc)})
    except composio.ComposioError as exc:
        # The connector is reachable or it is not; either way this is not a
        # fault in the caller's request.
        return _resp(502, {"error": "connector_unavailable", "detail": str(exc)})
    except A.QuotaExceeded as exc:
        return _resp(409, {"error": "quota_exceeded", "detail": str(exc)})
    except NotFound as exc:
        return _resp(404, {"error": "not_found", "detail": str(exc)})
    except PermissionError as exc:
        # The kill-switch (govern.Frozen) and offboarding (govern.PrincipalNotActive)
        # gates both subclass PermissionError, so freezing an org or acting as a
        # suspended principal surfaces here as 403 without a dedicated handler.
        return _resp(403, {"error": "forbidden", "detail": str(exc)})
    except Conflict as exc:
        return _resp(409, {"error": "conflict", "detail": str(exc)})
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return _resp(500, {"error": type(exc).__name__, "detail": str(exc)})


def _match(path: str, pattern: str) -> list[str] | None:
    rx = "^" + re.sub(r"\{(\w+)\}", r"([^/]+)", pattern) + "$"
    m = re.match(rx, path)
    # Decoded: connector ids contain a colon and browsers send it as %3A.
    return [urllib.parse.unquote(g) for g in m.groups()] if m else None


def _route(store: Store, method: str, path: str, body: dict, event: dict):
    # --- agents ------------------------------------------------------------
    if path == "/agents" and method == "GET":
        rows = store.query_index("gsi1", "gsi1pk", "AGENTS", limit=200)
        # Each row gains `managerId`: who it reports to (None: you). Worked out from
        # the whole org before the list is filtered, so a Bot's line is the same
        # whichever slice of the roster asked.
        rows = org.annotate(rows)
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
        agent = store.get(K.agent_pk(store.owner_id, p[0]), "META")
        agent["managerId"] = org.resolve(
            store.query_index("gsi1", "gsi1pk", "AGENTS", limit=200)).get(p[0])
        agent["memory"] = store.query(K.agent_pk(store.owner_id, p[0]), sk_prefix="MEM#")
        agent["grants"] = store.query(K.agent_pk(store.owner_id, p[0]), sk_prefix="GRANT#")
        agent["audit"] = store.query(K.agent_pk(store.owner_id, p[0]), sk_prefix="AUDIT#",
                                     limit=50, ascending=False)
        # Raw assignment rows, not `skills.assigned_active_skills` -- the
        # profile needs to show a skill pending approval or disabled too,
        # not only what the prompt is currently allowed to see.
        agent["skillAssignments"] = store.query(K.agent_pk(store.owner_id, p[0]), sk_prefix="SKILLASSIGN#")
        return _resp(200, agent)

    if (p := _match(path, "/agents/{id}")) and method == "PATCH":
        existing = store.get(K.agent_pk(store.owner_id, p[0]), "META")
        if "reportsTo" in body:
            # Against the whole org: the target must be an active Bot (or "owner")
            # and the move must not put a Bot under its own team.
            body = {**body, "reportsTo": org.validate(
                p[0], body["reportsTo"],
                store.query_index("gsi1", "gsi1pk", "AGENTS", limit=200))}
        changes, events = A.plan_update(existing, body, _actor(event))
        updated = store.update(K.agent_pk(store.owner_id, p[0]), "META", changes)
        for ev in events:
            store.put(ev)
        return _resp(200, updated)

    if (p := _match(path, "/agents/{id}")) and method == "DELETE":
        # Deactivation, never deletion. An agent that produced evidence must
        # remain something that evidence can point at.
        existing = store.get(K.agent_pk(store.owner_id, p[0]), "META")
        changes, events = A.plan_update(existing, {"status": "archived"},
                                        _actor(event))
        updated = store.update(K.agent_pk(store.owner_id, p[0]), "META", changes)
        for ev in events:
            store.put(ev)
        return _resp(200, updated)

    # --- a Bot's access to one app --------------------------------------------
    # A person narrows or removes it here; an agent has no route to this. The
    # ceiling comes from the org install, so a Bot can never be given more than the
    # organization holds, and "read" makes it read-only in that app whatever a
    # tool is called (connectors.authorize).
    if (p := _match(path, "/agents/{id}/grants/{connectorId}")) and method == "PUT":
        actor = _actor(event)
        store.get(K.agent_pk(store.owner_id, p[0]), "META")   # 404 if it is not this owner's Bot
        [grant] = A.validate_grants(
            [{"connectorId": p[1], "capability": body.get("capability"),
              "allowedTools": body.get("allowedTools") or [C.WILDCARD]}],
            _org_connectors(store))
        before = store.try_get(K.agent_pk(store.owner_id, p[0]), K.grant_sk(p[1]))
        row = store.put({"pk": K.agent_pk(store.owner_id, p[0]), "sk": K.grant_sk(p[1]), "entity": "Grant",
                         "agentId": p[0], "grantedBy": actor.user_id, "grantedAt": now_iso(), **grant})
        store.put(A.audit_event(p[0], "agent.grants_changed", actor,
                                before={"grant": before and {k: before.get(k) for k in ("capability", "allowedTools")}},
                                after={"grant": grant}))
        return _resp(200, row)

    if (p := _match(path, "/agents/{id}/grants/{connectorId}")) and method == "DELETE":
        actor = _actor(event)
        store.get(K.agent_pk(store.owner_id, p[0]), "META")
        before = store.get(K.agent_pk(store.owner_id, p[0]), K.grant_sk(p[1]))   # 404 if this Bot never had it
        store.delete(K.agent_pk(store.owner_id, p[0]), K.grant_sk(p[1]))
        store.put(A.audit_event(p[0], "agent.grants_changed", actor,
                                before={"grant": {k: before.get(k) for k in ("capability", "allowedTools")}},
                                after={"grant": None}))
        return _resp(200, {"agentId": p[0], "connectorId": p[1], "removed": True})

    # --- connectors --------------------------------------------------------
    # Ordered before /connectors/{id} so these never resolve as an id.
    if path == "/connectors/apps" and method == "GET":
        qs = event.get("queryStringParameters") or {}
        try:
            limit = int(qs.get("limit", "48"))
        except (TypeError, ValueError):
            return _resp(400, {"error": "invalid_request", "detail": "limit must be an integer"})
        return _resp(200, _composio().toolkits(
            search=qs.get("q"), cursor=qs.get("after"), limit=limit))

    if path == "/connectors" and method == "GET":
        return _resp(200, {"connectors": list(C.installed(store).values())})

    if path == "/connectors/connect-token" and method == "POST":
        # A hosted sign-in page for one app. The person authorizes the third
        # party there; the credential lives with Composio and is referenced here
        # only by account id.
        actor = _actor(event)
        slug = C.slug_of(body.get("connectorId") or "")
        link = _composio().connect_link(actor.user_id, slug,
                                        callback_url=_return_url(event, slug))
        store.put(C.connector_event(store.owner_id, C.connector_id(slug),
                                    "connector.authorization_started",
                                    actor_user_id=actor.user_id, detail="connect link issued"))
        return _resp(201, link)

    if path == "/connectors/accounts" and method == "GET":
        actor = _actor(event)
        qs = event.get("queryStringParameters") or {}
        return _resp(200, {"accounts": _composio().accounts(actor.user_id, toolkit=qs.get("app"))})

    if (p := _match(path, "/connectors/{id}")) and method == "GET":
        row = store.get(K.connector_pk(store.owner_id, p[0]), "META")
        row["log"] = store.query(K.connector_pk(store.owner_id, p[0]), sk_prefix="LOG#",
                                 limit=50, ascending=False)
        return _resp(200, row)

    if (p := _match(path, "/connectors/{id}/install")) and method == "POST":
        # Called once the person has finished signing in to the app. Nothing is
        # taken on trust from the browser: the connection is looked up with
        # Composio, and only an ACTIVE one is installed.
        actor = _actor(event)
        slug = C.slug_of(p[0])
        cid = C.connector_id(slug)
        client = _composio()
        accounts = client.accounts(actor.user_id, toolkit=slug)
        wanted = (body.get("accountId") or "").strip()
        account = next((a for a in accounts if not wanted or a["id"] == wanted), None)
        if account is None:
            return _resp(409, {"error": "not_connected",
                               "detail": f"{slug} is not connected yet. Finish signing in "
                                         "on the connect page, then try again."})
        row = C.install(store, cid, name=client.toolkit(slug)["name"], account_id=account["id"],
                        external_user_id=actor.user_id, actor_user_id=actor.user_id)
        granted = C.grant_to_active_agents(store, cid, actor_user_id=actor.user_id)
        store.put(C.connector_event(store.owner_id, cid, "connector.installed",
                                    actor_user_id=actor.user_id,
                                    detail=f"granted to: {granted or 'no new agents'}"))
        return _resp(201, {**row, "grantedTo": granted})

    if (p := _match(path, "/connectors/{id}")) and method == "DELETE":
        actor = _actor(event)
        store.get(K.connector_pk(store.owner_id, p[0]), "META")   # 404 if it is not ours
        result = C.revoke(store, p[0])
        store.put(C.connector_event(
            store.owner_id, p[0], "connector.revoked", actor_user_id=actor.user_id,
            detail=f"grants removed from: {result['revokedFrom'] or 'no agents'}"))
        return _resp(200, result)

    # --- memory ------------------------------------------------------------
    if (p := _match(path, "/agents/{id}/memory")) and method == "POST":
        row = _write_memory(store, K.agent_pk(store.owner_id, p[0]), body, scope="agent", actor=_actor(event))
        _event_in_dm(store, p[0], f"Saved to memory: {_label(row)}", icon="layers",
                     memId=row["memId"])
        return _resp(201, row)

    if (p := _match(path, "/agents/{id}/memory/{memId}")) and method == "PATCH":
        existing = store.get(K.agent_pk(store.owner_id, p[0]), K.memory_sk(p[1]))
        updated = store.update(K.agent_pk(store.owner_id, p[0]), K.memory_sk(p[1]), memory.plan_edit(existing, body))
        _event_in_dm(store, p[0], f"Memory corrected: {_label(updated)}", icon="layers",
                     memId=p[1])
        return _resp(200, updated)

    if (p := _match(path, "/agents/{id}/memory/{memId}")) and method == "DELETE":
        store.delete(K.agent_pk(store.owner_id, p[0]), K.memory_sk(p[1]))
        return _resp(204, {})

    if (p := _match(path, "/agents/{id}/memory/{memId}/revoke")) and method == "POST":
        return _resp(200, store.update(K.agent_pk(store.owner_id, p[0]), K.memory_sk(p[1]), memory.revoke()))

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
        if isinstance(source, str) and store.try_get(K.thread_pk(store.owner_id, source), "META"):
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
            marker = store.try_get(K.thread_pk(store.owner_id, thread["threadId"]), "READ") or {}
            thread["readAt"] = marker.get("readAt")
            thread["unread"] = bool(
                thread.get("lastActivity")
                and thread["lastActivity"] > (marker.get("readAt") or ""))
        return _resp(200, {"threads": rows})

    if path == "/threads" and method == "POST":
        agent_ids = body.get("agentIds", [])
        if not isinstance(agent_ids, list) or not all(isinstance(a, str) for a in agent_ids):
            raise A.ValidationError("agentIds must be a list of agent ids")
        # Stored as counted. A room saved with repeats is a room whose size
        # every later check misreads -- and one that wakes a Bot once per copy.
        agent_ids = list(dict.fromkeys(agent_ids))
        if len(agent_ids) > collab.MAX_ROOM_MEMBERS:
            raise A.ValidationError(
                f"a room holds at most {collab.MAX_ROOM_MEMBERS} agents")
        thread_id = new_id("th_")
        actor = _actor(event)
        # A room has no single runtime session: every logical Bot in it gets a
        # distinct owner/Bot/thread session when a run starts. A one-Bot task
        # can expose its session for the Computer surface.
        session = ({"sessionId": K.bot_session_id(store.owner_id, agent_ids[0], thread_id)}
                   if len(agent_ids) == 1 else {})
        return _resp(201, store.put({
            "pk": K.thread_pk(store.owner_id, thread_id), "sk": "META",
            "entity": "Thread", "threadId": thread_id,
            "gsi1pk": "THREADS", "gsi1sk": now_iso(),
            "kind": body.get("kind", "dm"),
            "title": body.get("title", "New task"),
            "agentIds": agent_ids,
            **session,
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
        thread = store.get(K.thread_pk(store.owner_id, p[0]), "META")
        # Agent-to-agent traffic (`AgentMessage`) is a coordination event, not
        # a chat turn -- see /threads/{id}/coordination. Mixing it into
        # `messages` would render it as an ordinary bubble in the room the
        # owner reads, which is exactly the "looks like a shared DM" framing
        # this route must not produce.
        rows = store.query(K.thread_pk(store.owner_id, p[0]), sk_prefix="MSG#", limit=200)
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
        store.get(K.thread_pk(store.owner_id, p[0]), "META")   # 404 if the thread is not ours
        agent_messages = [r for r in store.query(K.thread_pk(store.owner_id, p[0]), sk_prefix="MSG#", limit=200)
                          if r.get("entity") == "AgentMessage"]
        thread_runs = [r for r in store.query_index("gsi1", "gsi1pk", "RUNS", limit=500)
                      if r.get("threadId") == p[0]]
        handoff_rows = [(r, h) for r in thread_runs
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
              } for r, h in handoff_rows),
        ]
        items.sort(key=lambda i: i.get("at") or "")
        return _resp(200, {"coordination": items})

    # --- tasks (durable, multi-run coordination) ----------------------
    # Every task a fan-out has ever created, newest first -- the gsi1 listing
    # `handoffs.ensure_task`/`close_task_if_finished` keep current. Same shape
    # as GET /approvals: one index read, filtered by status in code rather
    # than as a second index key, since task volume is workspace-scale.
    if path == "/tasks" and method == "GET":
        qs = event.get("queryStringParameters") or {}
        wanted = qs.get("status")
        rows = store.query_index("gsi1", "gsi1pk", "TASKS", limit=200)
        matched = [t for t in rows if t.get("status") == wanted] if wanted else rows
        matched.sort(key=lambda t: t.get("createdAt") or "", reverse=True)
        return _resp(200, {"tasks": matched})

    # Read-only. The parent/coordinator's own visible "still working"
    # state while fan-out children are outstanding: `pendingChildren` is the
    # same counter `handoffs.accept`/`notify_coordinator_if_child` maintain
    # for the race-free wake -- this route just reads it back.
    if (p := _match(path, "/tasks/{id}")) and method == "GET":
        task = store.get(K.task_pk(p[0]), "META")
        children = store.query(K.task_pk(p[0]), sk_prefix="CHILD#", limit=50)
        counts = {"active": 0, "done": 0, "failed": 0, "cancelled": 0}
        for c in children:
            counts[c.get("status", "active")] = counts.get(c.get("status", "active"), 0) + 1
        task["pendingChildren"] = max(0, task.get("pendingChildren", 0))
        task["children"] = children
        task["counts"] = counts
        task["artifacts"] = [_artifact_card(_artifact_s3(), a)
                             for a in handoffs.list_artifacts_for_task(store, p[0])]
        return _resp(200, task)

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

    # --- handoffs ------------------------------------------------------
    # A human decision on a handoff `_record_handoff` left `proposed` --
    # most never reach here, because `orchestrator._handle_tool` already
    # tried `handoffs.can_auto_accept` at the moment it was proposed. This
    # is the fallback for the ones that could not be resolved automatically.
    if (p := _match(path, "/handoffs/{runId}/{hoffId}")) and method == "POST":
        return _decide_handoff(store, p[0], p[1], body, _actor(event))

    # --- read state --------------------------------------------------------
    # The inbox orders on lastActivity and has never had anything to compare
    # it against, so every conversation looked equally attended to. A marker
    # per thread is the whole feature: unread is `lastActivity > readAt`,
    # derived on read rather than stored, so it cannot drift from the
    # activity it describes.
    if (p := _match(path, "/threads/{id}/read")) and method == "POST":
        thread = store.get(K.thread_pk(store.owner_id, p[0]), "META")   # 404 if not ours
        at = body.get("at") or thread.get("lastActivity") or now_iso()
        marker = store.put({
            "pk": K.thread_pk(store.owner_id, p[0]), "sk": "READ",
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
        agent = store.get(K.agent_pk(store.owner_id, record["agentId"]), "META")   # 404 for a foreign agent
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

    # --- files -------------------------------------------------------------
    # Only named files a Bot produced live in this library. A sealed evidence
    # manifest is an audit record, not a document, and conversations belong in
    # their threads rather than being presented as files.
    if path == "/artifacts" and method == "GET":
        qs = event.get("queryStringParameters") or {}
        return _resp(200, {"artifacts": _artifacts(
            store, status=qs.get("status"), run_id=qs.get("runId"),
            artifact_type=qs.get("artifactType"))})

    if (p := _match(path, "/artifacts/{id}")) and method == "GET":
        row = store.get(K.artifact_pk(p[0]), "META")  # 404s, owner-scoped, if not ours
        return _resp(200, _artifact_card(_artifact_s3(), row))

    if (p := _match(path, "/artifacts/{id}")) and method == "DELETE":
        row = AR.soft_delete(store, p[0])
        return _resp(200, {"artifactId": row["artifactId"], "status": row["status"]})

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

    # --- allowlist -----------------------------------------------------
    # Who may sign in at all -- the gate `identity.assert_owner` checks
    # before any route (including this one) is ever reached, so getting
    # here already proves the caller is allowed. Managing the list is
    # therefore no more privileged than anything else an owner can already
    # do; there is no separate admin role yet (that is PR-scoped elsewhere).
    if path == "/allowlist" and method == "GET":
        return _resp(200, {"allowlist": identity.list_allowed()})

    if path == "/allowlist" and method == "POST":
        value = (body.get("value") or body.get("email") or "").strip()
        if not value:
            return _resp(400, {"error": "invalid_request", "detail": "value is required"})
        actor = _actor(event)
        entry = identity.allow(value, added_by=actor.user_id)
        return _resp(201, entry)

    if (p := _match(path, "/allowlist/{value}")) and method == "DELETE":
        identity.disallow(p[0])
        return _resp(204, {})

    # --- usage -------------------------------------------------------------
    if path == "/usage" and method == "GET":
        qs = event.get("queryStringParameters") or {}
        agent_id = qs.get("agentId")
        month = qs.get("month") or now_iso()[:7]
        if not agent_id:
            return _resp(400, {"error": "agentId is required"})
        rows = store.query(K.cost_pk(store.owner_id, agent_id, month), limit=500)
        total = sum(float(r.get("totalUsd", 0.0)) for r in rows)
        return _resp(200, {"agentId": agent_id, "month": month,
                           "totalUsd": round(total, 4), "runs": rows})

    # --- billing -------------------------------------------------------------
    # Self-service, per-account: every owner sees and manages only their own
    # balance and subscription. No cross-account view exists yet -- see
    # identity.load_membership's own "single-tenant seam" note; that is a
    # deliberately separate, larger piece of work.
    if path == "/billing" and method == "GET":
        row = billing.ensure_billing_row(store)
        return _resp(200, {
            "balanceUsd": billing.balance_usd(store),
            "creditsRemaining": billing.credits_remaining(store),
            "tier": row.get("tier"),
            "subscriptionStatus": row.get("subscriptionStatus"),
            "hasCredit": billing.has_credit(store),
        })

    if path == "/billing/ledger" and method == "GET":
        qs = event.get("queryStringParameters") or {}
        limit = min(int(qs.get("limit", 100)), 200)
        return _resp(200, {"entries": billing.ledger(store, limit=limit)})

    if path == "/billing/plans" and method == "GET":
        # What a plan/top-up costs and whether it can actually be checked
        # out yet -- a null stripePriceId (scripts/stripe_setup.py has not
        # run against this account) is shown, never hidden, so the console
        # can say why a button is disabled instead of just disabling it.
        plans = billing.load_plans()
        return _resp(200, plans)

    if path == "/billing/checkout" and method == "POST":
        origin = _billing_origin(event)
        if not origin:
            return _resp(400, {"error": "invalid_request", "detail": "unrecognized origin"})
        try:
            url = billing.start_checkout(
                store, plan_key=body.get("planKey"), top_up_key=body.get("topUpKey"),
                success_url=f"{origin}/billing?checkout=success",
                cancel_url=f"{origin}/billing?checkout=cancelled",
                customer_email=_principal(event).email)
        except ValueError as exc:
            return _resp(400, {"error": "invalid_request", "detail": str(exc)})
        # StripeError before RuntimeError: it is a RuntimeError subclass, so
        # the reverse order would let this first except swallow it.
        except stripe_client.StripeError as exc:
            return _resp(502, {"error": "stripe_unavailable", "detail": str(exc)})
        except RuntimeError as exc:
            return _resp(409, {"error": "not_purchasable", "detail": str(exc)})
        return _resp(200, {"url": url})

    if path == "/billing/portal" and method == "POST":
        origin = _billing_origin(event)
        if not origin:
            return _resp(400, {"error": "invalid_request", "detail": "unrecognized origin"})
        try:
            url = billing.start_portal(store, return_url=f"{origin}/billing")
        except stripe_client.StripeError as exc:
            return _resp(502, {"error": "stripe_unavailable", "detail": str(exc)})
        except RuntimeError as exc:
            return _resp(409, {"error": "no_subscription", "detail": str(exc)})
        return _resp(200, {"url": url})

    # --- admin governance --------------------------------------------------
    # The Directory, the org kill switch, and the admin audit trail. Every
    # route re-derives the caller from the verified token via _membership()
    # and gates on the FEAT-001 capability matrix; the {subject} in a path is
    # the TARGET being acted on, never the actor. Specific literals are
    # registered before the parameterized /admin/directory/{subject} so an
    # /invites POST never resolves as a subject.
    if path == "/admin/directory" and method == "GET":
        return _admin_list_members(store, event)

    if path == "/admin/directory/invites" and method == "POST":
        return _admin_invite(store, body, event)

    if (p := _match(path, "/admin/directory/{subject}/suspend")) and method == "POST":
        return _admin_suspend(store, p[0], body, event)

    if (p := _match(path, "/admin/directory/{subject}/reactivate")) and method == "POST":
        return _admin_reactivate(store, p[0], body, event)

    if (p := _match(path, "/admin/directory/{subject}")) and method == "PATCH":
        return _admin_change_role(store, p[0], body, event)

    if path == "/admin/killswitch" and method == "GET":
        return _admin_killswitch_status(store, event)

    if path == "/admin/killswitch" and method == "POST":
        return _admin_killswitch_set(store, body, event)

    if path == "/admin/audit" and method == "GET":
        return _admin_audit(store, event)

    # --- admin agent lifecycle actions -------------------------------------
    # The operator's two direct asks: put a bot back through onboarding, and
    # archive a bot's accumulated memory. Both are gated on the RBAC matrix and
    # audited; neither ever deletes an AUDIT#/evidence row (docs/architecture/10).
    # The literal /entrypoint routes are registered BEFORE the parameterized
    # /{id} routes so "entrypoint" is never captured as an agent id. They let
    # the console act on the caller's own entrypoint Bot without knowing its
    # id -- the server resolves it from the caller's org (never from the body).
    if path == "/admin/agents/entrypoint/reset-onboarding" and method == "POST":
        return _admin_reset_onboarding_entrypoint(store, body, event)

    if path == "/admin/agents/entrypoint/archive-memory" and method == "POST":
        return _admin_archive_memory_entrypoint(store, body, event)

    if (p := _match(path, "/admin/agents/{id}/reset-onboarding")) and method == "POST":
        return _admin_reset_onboarding(store, p[0], body, event)

    if (p := _match(path, "/admin/agents/{id}/archive-memory")) and method == "POST":
        return _admin_archive_memory(store, p[0], body, event)

    return _resp(404, {"error": "no such route", "path": path, "method": method})


# --- admin governance helpers ----------------------------------------------
#
# The Directory RBAC engine, the kill switch and the admin audit trail from
# FEAT-001 wired to HTTP. directory.assert_can raises Escalation (403) and the
# last-Owner floor raises ValidationError (400); both already map in handler().


def _membership(store: Store, event: dict) -> "D.Membership":
    """The acting caller's standing in their org, re-derived from the verified
    token on every admin request.

    guard_principal is the pull-based offboarding cut: a human suspended a
    moment ago holds nothing, so their very next admin action fails closed here
    (403) rather than at some later sync. Never trust a subject from the path
    or body -- identity.load_membership keys on principal.user_id.
    """
    membership = identity.load_membership(store, _principal(event))
    govern.guard_principal(membership)
    return membership


def _admin_org_id(event: dict) -> str:
    """The one org id every admin route keys on, read AND write.

    Derived from the verified principal (== the caller's subject today, the
    one-workspace-per-owner seam). Both the read paths (killswitch status,
    audit, roster) and the write paths use THIS, not `store.owner_id`, so the
    two do not encode two different notions of "the org" that would diverge
    the moment org_id != user_id. `store.owner_id` still enforces ownerId
    isolation on the rows underneath; this only decides which org partition
    the admin surface addresses.
    """
    return _principal(event).org_id


def _correlation_id(event: dict) -> str | None:
    """The client's X-Correlation-Id if it supplied one, else None so
    admin_audit_event generates one. Either way the id is echoed back on a
    state change so a UI can link the admin action to the run evidence it
    causes (docs/architecture/10)."""
    return _header(event, "x-correlation-id") or None


def _with_reason(base: str, body: dict) -> str:
    """Fold the operator's typed reason into a system-generated audit detail.

    The confirm dialog collects a free-text reason precisely so the
    append-only trail records WHY, not just what. The subject/agent id is
    still taken from the path (never from the body); only the reason is
    body-supplied. A blank or missing reason leaves the base detail untouched,
    so the row is never worse than before this change.
    """
    reason = (body or {}).get("reason")
    if isinstance(reason, str) and reason.strip():
        return f"{base} (reason: {reason.strip()})"
    return base


def _parse_role(value, *, default: "D.Role | None" = None) -> "D.Role":
    """A `directory.Role` from client input, or a 400 with a clear message.

    Left to `D.Role(value)` a bad string raises a bare ValueError that falls
    through to the generic 500 handler -- a malformed request is the caller's
    error, not the server's, so it must surface as A.ValidationError -> 400.
    """
    if value is None and default is not None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise A.ValidationError("a 'role' is required")
    try:
        return D.Role(value.strip())
    except ValueError:
        allowed = ", ".join(r.value for r in D.Role)
        raise A.ValidationError(f"unknown role {value!r}; expected one of {allowed}")


def _member_view(row: dict) -> dict:
    """A member-facing projection of a stored MEMBER# row.

    The stored row carries internal machinery -- ownerId, pk/sk, gsi1pk/gsi1sk,
    createdAt/updatedAt -- that is nobody's business outside the store and
    leaks the single-table layout to the client. Project only the fields a
    directory UI needs, so widening the row later does not silently start
    shipping new internals over the wire.
    """
    return {
        "subject": row.get("subject"),
        "role": row.get("role"),
        "scope": row.get("scope"),
        "scopeId": row.get("scopeId"),
        "state": row.get("state"),
        "invitedBy": row.get("invitedBy"),
        "invitedAt": row.get("invitedAt"),
    }


def _audit_view(row: dict) -> dict:
    """A client projection of a stored admin-audit row. Same reason as
    _member_view: the envelope the audit reader needs is the action, actor,
    timing, correlation id and before/after -- not pk/sk/ownerId/gsi keys."""
    return {
        "action": row.get("action"),
        "at": row.get("at"),
        "actorUserId": row.get("actorUserId"),
        "actorAgentId": row.get("actorAgentId"),
        "correlationId": row.get("correlationId"),
        "before": row.get("before"),
        "after": row.get("after"),
        "detail": row.get("detail"),
        "v": row.get("v"),
    }


def _roster(store: Store, org_id: str) -> list["D.Membership"]:
    """The org's current members, as Memberships -- passed to the last-Owner
    floor so suspend/demote can refuse to strip an org of its final Owner."""
    rows = store.query(K.org_pk(org_id), sk_prefix="MEMBER#", limit=500)
    return [D.membership_of(r) for r in rows]


def _admin_list_members(store: Store, event: dict):
    """The full roster. Gated on READ_AUDIT_LOG rather than a member-only
    capability: seeing who else is in the org and their roles is a governance
    read (Owner/Admin/Security/Auditor), and a plain Member must not enumerate
    the directory. Rows are projected through _member_view so internal store
    fields never reach the client."""
    D.assert_can(_membership(store, event), D.Capability.READ_AUDIT_LOG)
    org_id = _admin_org_id(event)
    rows = store.query(K.org_pk(org_id), sk_prefix="MEMBER#", limit=500)
    return _resp(200, {"members": [_member_view(r) for r in rows]})


def _admin_invite(store: Store, body: dict, event: dict):
    """Invite a human. Requires INVITE_USERS (Owner/Admin per the matrix).

    Input is validated before anything is written: an unknown role is a 400
    (not a bare ValueError -> 500), inviting yourself is refused (you are
    already seated as the actor), and re-inviting an already-seated subject is
    a clear 409 rather than an opaque transaction-cancelled Conflict.
    """
    actor = _actor(event)
    D.assert_can(_membership(store, event), D.Capability.INVITE_USERS)
    subject = (body.get("subject") or body.get("email") or "").strip()
    if not subject:
        raise A.ValidationError("an invite needs a 'subject' or 'email'")
    role = _parse_role(body.get("role"), default=D.Role.MEMBER)
    org_id = _admin_org_id(event)

    # Self-invite is meaningless: the actor already holds a seat (they had to,
    # to reach this route). Refuse it as a bad request rather than writing a
    # second row that would collide with or shadow their own membership.
    if subject == actor.user_id:
        raise A.ValidationError("you cannot invite yourself")

    # An already-seated subject is a duplicate, surfaced as a clear 409 instead
    # of the create-only transaction's opaque "transaction cancelled".
    if store.try_get(K.org_pk(org_id), K.member_sk(subject)) is not None:
        raise Conflict(f"{subject} already has a seat in this org")

    row = D.invite_member(org_id, subject, role, invited_by=actor.user_id)
    corr = _correlation_id(event)
    audit = govern.admin_audit_event(org_id, "member.invited", actor,
                                     correlation_id=corr,
                                     after={"subject": subject, "role": role.value},
                                     detail="member invited")
    # Atomic: the member row and its audit row land together or neither does,
    # the same shape as agent create.
    store.transact_put([row, audit])
    return _resp(201, {"member": _member_view(row),
                       "correlationId": audit["correlationId"]})


def _admin_suspend(store: Store, subject: str, body: dict, event: dict):
    """Offboard a human. Mapped to TERMINATE_COMPUTER: it is the closest
    offboarding control in the matrix (Owner/Admin/Security), and suspending a
    seat is the human analogue of terminating a running computer -- both are
    "stop this actor now". The seat becomes SUSPENDED, never deleted, so the
    audit trail survives it.

    The subject is the TARGET, read from the path; the optional free-text
    reason on the body is the operator's justification and is folded into the
    audit detail so the trail records why the seat was suspended."""
    actor = _actor(event)
    D.assert_can(_membership(store, event), D.Capability.TERMINATE_COMPUTER)
    org_id = _admin_org_id(event)
    row = store.get(K.org_pk(org_id), K.member_sk(subject))   # 404 if unknown
    target = D.membership_of(row)
    patch = D.suspend_patch(target, _roster(store, org_id))   # last-Owner floor
    updated = _apply_member_change(
        store, org_id, subject, patch, actor, event,
        action="member.suspended",
        before={"state": target.state.value},
        after={"state": patch["state"]},
        detail=_with_reason(f"suspended {subject}", body))
    return _resp(200, updated)


def _admin_reactivate(store: Store, subject: str, body: dict, event: dict):
    """Return an invited or suspended seat to ACTIVE. Requires INVITE_USERS:
    reactivating is the same "who may seat a human" authority as inviting
    (Owner/Admin), so it shares that capability rather than the offboarding
    one.

    The subject is the TARGET, read from the path; the optional free-text
    reason on the body is folded into the audit detail."""
    actor = _actor(event)
    D.assert_can(_membership(store, event), D.Capability.INVITE_USERS)
    org_id = _admin_org_id(event)
    row = store.get(K.org_pk(org_id), K.member_sk(subject))   # 404 if unknown
    target = D.membership_of(row)
    patch = D.reactivate_patch(target)
    updated = _apply_member_change(
        store, org_id, subject, patch, actor, event,
        action="member.reactivated",
        before={"state": target.state.value},
        after={"state": patch["state"]},
        detail=_with_reason(f"reactivated {subject}", body))
    return _resp(200, updated)


def _admin_change_role(store: Store, subject: str, body: dict, event: dict):
    """Change a seat's role. Requires INVITE_USERS (Owner/Admin): assigning a
    role is the same seating authority as inviting. A missing or unknown role
    is a 400, not a hard KeyError/ValueError -> 500. The last-Owner floor
    refuses to demote the final Owner."""
    actor = _actor(event)
    D.assert_can(_membership(store, event), D.Capability.INVITE_USERS)
    new_role = _parse_role(body.get("role"))
    org_id = _admin_org_id(event)
    row = store.get(K.org_pk(org_id), K.member_sk(subject))   # 404 if unknown
    target = D.membership_of(row)
    patch = D.change_role_patch(target, new_role, _roster(store, org_id))
    updated = _apply_member_change(
        store, org_id, subject, patch, actor, event,
        action="member.role_changed",
        before={"role": target.role.value},
        after={"role": patch["role"]},
        detail=_with_reason(f"role of {subject} -> {new_role.value}", body))
    return _resp(200, updated)


def _apply_member_change(store: Store, org_id: str, subject: str, patch: dict,
                         actor: A.Actor, event: dict, *, action: str,
                         before: dict, after: dict, detail: str) -> dict:
    """Persist a member state change and its audit row, audit-FIRST.

    The invariant is "the audit is what must never be lost." DynamoDB cannot
    mix a conditional Update of an existing MEMBER# row with a create-only Put
    of the audit row in one transaction the way agent-create's all-Puts
    transaction does, so full atomicity is not available through the current
    Store API here. Given that, the audit row is written BEFORE the state
    change: a crash between the two over-records (an audit row whose state
    change did not land) rather than under-records (a state change with no
    trail). Over-recording is the safe direction for an append-only trail --
    the alternative loses the one thing the system promises to keep.
    """
    corr = _correlation_id(event)
    audit = govern.admin_audit_event(org_id, action, actor,
                                     correlation_id=corr,
                                     before=before, after=after, detail=detail)
    store.put(audit)
    updated = store.update(K.org_pk(org_id), K.member_sk(subject), patch)
    return {"member": _member_view(updated), "correlationId": audit["correlationId"]}


def _admin_killswitch_status(store: Store, event: dict):
    """The org's freeze state. Gated on CHANGE_ORG_POLICIES rather than a plain
    read: whether the org is frozen is an org-policy fact, and the roles that
    may flip it (Owner/Admin/Security) are the ones that should see it."""
    D.assert_can(_membership(store, event), D.Capability.CHANGE_ORG_POLICIES)
    org_id = _admin_org_id(event)
    row = store.try_get(K.org_pk(org_id), "KILLSWITCH")
    return _resp(200, {"frozen": govern.is_frozen(row),
                       "reason": (row or {}).get("reason", ""),
                       "setBy": (row or {}).get("setBy"),
                       "setAt": (row or {}).get("setAt")})


def _admin_killswitch_set(store: Store, body: dict, event: dict):
    """Freeze or unfreeze the org. Requires CHANGE_ORG_POLICIES
    (Owner/Admin/Security). Freezing fails every gated bot action closed on its
    next run (see orchestrator._drive)."""
    actor = _actor(event)
    D.assert_can(_membership(store, event), D.Capability.CHANGE_ORG_POLICIES)
    frozen = bool(body.get("frozen"))
    org_id = _admin_org_id(event)
    before = store.try_get(K.org_pk(org_id), "KILLSWITCH")
    row = govern.killswitch_row(org_id, frozen=frozen, actor=actor,
                                reason=(body.get("reason") or ""))
    corr = _correlation_id(event)
    audit = govern.admin_audit_event(
        org_id, "org.frozen" if frozen else "org.unfrozen", actor,
        correlation_id=corr,
        before={"frozen": govern.is_frozen(before)}, after={"frozen": frozen},
        detail=(body.get("reason") or ""))
    # The kill switch is a single overwriteable row (a re-freeze replaces it),
    # so it is a put rather than a transact_put's create-only write. The two
    # writes are ordered so a crash between them leaves the org in the MORE
    # restrictive (frozen) state -- a kill switch must fail closed on the
    # action, not the audit. That makes the ordering asymmetric:
    #
    #   FREEZE (frozen=True): write the KILLSWITCH row FIRST, then the audit.
    #     A crash after the state write leaves the org actually frozen with at
    #     worst a missing audit row -- the org is safely stopped, which is the
    #     priority. Audit-first here would be wrong: a crash would leave the
    #     org unfrozen while the trail claims a freeze, so an operator believes
    #     the org is stopped when it is not.
    #   UNFREEZE (frozen=False): keep audit-FIRST. A crash after the audit
    #     leaves the org still frozen with a trail claiming it was unfrozen --
    #     again the safe direction, since staying frozen is the restrictive
    #     state and the append-only trail is not lost.
    if frozen:
        written = store.put(row)
        store.put(audit)
    else:
        store.put(audit)
        written = store.put(row)
    return _resp(200, {"frozen": frozen, "reason": written.get("reason", ""),
                       "correlationId": audit["correlationId"]})


def _admin_audit(store: Store, event: dict):
    """Read-only, newest-first admin history. Requires READ_AUDIT_LOG so the
    read-only Auditor can see it (Owner/Admin/Security/Auditor) and a plain
    Member cannot. Rows are projected through _audit_view so store internals
    (pk/sk/ownerId/gsi keys) never reach the client."""
    D.assert_can(_membership(store, event), D.Capability.READ_AUDIT_LOG)
    org_id = _admin_org_id(event)
    rows = store.query(K.org_pk(org_id), sk_prefix="ADMINAUDIT#",
                       ascending=False, limit=500)
    return _resp(200, {"audit": [_audit_view(r) for r in rows]})


def _resolve_entrypoint_agent(store: Store) -> dict:
    """The caller's own entrypoint Bot ('Chief'), resolved server-side.

    The admin console has no agent listing to key a button on, and the caller's
    subject is NOT an agent id (agent ids come from agents.normalize_agent_id of
    the Bot's name, e.g. 'chief'), so a reset button cannot know the id up
    front. Rather than trust an id from the request, the server derives the
    entrypoint from the caller's own org exactly the way GET /agents does --
    the AGENTS gsi1 index, filtered to `entrypoint is True`. There is at most
    one entrypoint per org (agents.plan_create enforces the single-first-Bot
    rule), so the match is unambiguous. A NotFound here means the org has no
    entrypoint Bot yet, which surfaces as a 404 rather than a silent no-op.
    """
    rows = store.query_index("gsi1", "gsi1pk", "AGENTS", limit=200)
    for row in rows:
        if row.get("entrypoint") is True:
            return store.get(K.agent_pk(store.owner_id, row["agentId"]), "META")
    raise NotFound("this organization has no entrypoint Bot to reset")


def _admin_reset_onboarding(store: Store, agent_id: str, body: dict, event: dict):
    """Put the entrypoint Bot back through onboarding.

    `agent_id` is a real agent id from the path (the Bot's slugged name, e.g.
    'chief'), never the caller's subject. When the console cannot supply an id
    it hits the /admin/agents/entrypoint/reset-onboarding route, which resolves
    the caller's own entrypoint Bot server-side and calls
    `_reset_onboarding_agent` directly -- so this path always addresses a genuine
    agent, and the two identifier spaces never mix.
    """
    actor = _actor(event)
    # Gate BEFORE any read or write, in the documented order the member-write
    # handlers use: guard_principal (via _membership) -> assert_not_frozen ->
    # assert_can. The kill switch is the outermost gate, so a frozen org is
    # rejected even for a principal who would also fail RBAC.
    membership = _membership(store, event)
    govern.assert_not_frozen(store.try_get(K.org_pk(_admin_org_id(event)), "KILLSWITCH"))
    D.assert_can(membership, D.Capability.CHANGE_ORG_POLICIES)

    agent = store.get(K.agent_pk(store.owner_id, agent_id), "META")   # 404 if not this owner's
    if agent.get("entrypoint") is not True:
        # 'Chief' is the onboarding entrypoint; only it carries the brief and
        # the closest-fit greeting, so only it can be reset back into it.
        raise A.ValidationError(
            "only the entrypoint Bot can be reset to onboarding")
    return _reset_onboarding_agent(store, agent, body, event)


def _admin_reset_onboarding_entrypoint(store: Store, body: dict, event: dict):
    """Reset the caller's own entrypoint Bot without the caller knowing its id.

    reset-onboarding is inherently about the entrypoint 'Chief' -- the handler
    already requires `agent['entrypoint'] is True` -- so the admin action does
    not need the caller to supply the agent id at all. The same gates run FIRST
    (guard_principal -> assert_not_frozen -> assert_can), then the entrypoint is
    resolved from the caller's own org via the store (never from the body), and
    the shared reset logic runs on it. The entrypoint is, by construction, an
    entrypoint Bot, so the 400 guard cannot fire here.
    """
    actor = _actor(event)  # noqa: F841 -- symmetry with _admin_reset_onboarding
    membership = _membership(store, event)
    govern.assert_not_frozen(store.try_get(K.org_pk(_admin_org_id(event)), "KILLSWITCH"))
    D.assert_can(membership, D.Capability.CHANGE_ORG_POLICIES)

    agent = _resolve_entrypoint_agent(store)
    return _reset_onboarding_agent(store, agent, body, event)


def _reset_onboarding_agent(store: Store, agent: dict, body: dict, event: dict):
    """Run the reset on an already-resolved, already-authorized entrypoint Bot.

    The reset touches exactly two things: the systemPrompt goes back to
    onboarding.brief() so the next run's is_brief() is true again, and the
    dm-<agentId> starter thread's conversational Message rows are cleared and
    re-seeded with a fresh starter greeting. AUDIT#/evidence rows are never
    touched -- they are append-only and outlive what they describe
    (docs/architecture/10). Callers MUST run the RBAC/kill-switch/principal
    gates before reaching here.
    """
    actor = _actor(event)
    agent_id = agent["agentId"]

    # (1) systemPrompt back to the onboarding brief. plan_update keeps this a
    # first-class edit (systemPrompt IS in agents.PATCHABLE) so the audit trail
    # and validation are identical to any other prompt change.
    fresh_prompt = onboarding.brief(agent["name"])
    changes, events = A.plan_update(agent, {"systemPrompt": fresh_prompt}, actor)
    store.update(K.agent_pk(store.owner_id, agent_id), "META", changes)
    for ev in events:
        store.put(ev)

    # (2) clear the starter thread's Message rows and re-seed a fresh greeting,
    # mirroring the exact shapes agents.plan_create builds. Only Message rows
    # in the dm partition are removed -- there are no AUDIT#/evidence rows in a
    # thread partition, so nothing append-only is at risk here.
    thread_id = f"dm-{agent_id}"
    thread_pk = K.thread_pk(store.owner_id, thread_id)
    existing_msgs = store.query(thread_pk, sk_prefix="MSG#", limit=500)
    for msg in existing_msgs:
        store.delete(thread_pk, msg["sk"])

    # The operator's display name is only known at create time (from the
    # create body); a reset has no such body, so the greeting is the generic
    # unnamed form rather than guessing a name from the token.
    text, suggestions = onboarding.starter_message(entrypoint=True)
    greeting = {
        "pk": thread_pk,
        "sk": K.message_sk(now_iso(), ordered_suffix()),
        "entity": "Message", "role": "assistant",
        "author": agent["name"], "agentId": agent_id,
        "text": text, "starter": True,
    }
    if suggestions:
        greeting["suggestions"] = suggestions
    store.put(greeting)
    # The thread META's preview/timestamp follow the new greeting, so the
    # reset thread reads as freshly unread just like a brand-new Bot.
    store.update(thread_pk, "META", threads.touch(text, "assistant"))

    # (3) audit LAST: the reset is done, and the trail records what happened.
    org_id = _admin_org_id(event)
    audit = govern.admin_audit_event(
        org_id, "onboarding.reset", actor,
        correlation_id=_correlation_id(event),
        before={"messages": len(existing_msgs)},
        after={"systemPrompt": "brief", "messages": 1},
        detail=_with_reason(f"reset {agent_id} to onboarding", body))
    store.put(audit)
    return _resp(200, {"agentId": agent_id, "reset": True,
                       "clearedMessages": len(existing_msgs),
                       "correlationId": audit["correlationId"]})


def _admin_archive_memory(store: Store, agent_id: str, body: dict, event: dict):
    """Archive a Bot's accumulated memory -- revoke, never destroy.

    `agent_id` is a real agent id from the path (the Bot's slugged name), never
    the caller's subject. The console resolves the id by fetching GET /agents
    and letting the operator pick which Bot to archive; when no id can be picked
    it hits /admin/agents/entrypoint/archive-memory, which resolves the
    entrypoint 'Chief' server-side. Either way the id is a genuine agent id, so
    the K.agent_pk lookup here always resolves for a real click.
    """
    actor = _actor(event)  # noqa: F841 -- symmetry with the entrypoint route
    # Gate FIRST, in the documented order the member-write handlers use:
    # guard_principal (via _membership) -> assert_not_frozen -> assert_can.
    # The kill switch is the outermost gate, so a frozen org is rejected even
    # for a principal who would also fail RBAC; all three still precede the
    # agent lookup and any revoke or audit write.
    membership = _membership(store, event)
    govern.assert_not_frozen(store.try_get(K.org_pk(_admin_org_id(event)), "KILLSWITCH"))
    D.assert_can(membership, D.Capability.TERMINATE_COMPUTER)

    agent = store.get(K.agent_pk(store.owner_id, agent_id), "META")   # 404 if not this owner's
    return _archive_memory_agent(store, agent, body, event)


def _admin_archive_memory_entrypoint(store: Store, body: dict, event: dict):
    """Archive the caller's own entrypoint Bot's memory without knowing its id.

    Same gates FIRST (guard_principal -> assert_not_frozen -> assert_can), then
    the entrypoint 'Chief' is resolved from the caller's own org via the store
    (never from the body), and the shared revoke logic runs on it. This is the
    minimal working fallback for the single-tenant console, which labels the
    action as targeting Chief.
    """
    membership = _membership(store, event)
    govern.assert_not_frozen(store.try_get(K.org_pk(_admin_org_id(event)), "KILLSWITCH"))
    D.assert_can(membership, D.Capability.TERMINATE_COMPUTER)

    agent = _resolve_entrypoint_agent(store)
    return _archive_memory_agent(store, agent, body, event)


def _archive_memory_agent(store: Store, agent: dict, body: dict, event: dict):
    """Revoke every published memory an already-resolved, already-authorized Bot
    owns. Callers MUST run the RBAC/kill-switch/principal gates first.

    Archive == revoke: every currently-published memory the Bot owns (its
    agent-scope rows, and any shared_user rows it authored) has memory.revoke()
    applied via store.update, so is_visible() -> False and the fact leaves the
    prompt on the very next turn. Nothing is hard-deleted, and evidence/AUDIT#
    rows are never touched (append-only, permanent per docs/architecture/10).
    """
    actor = _actor(event)
    agent_id = agent["agentId"]

    # The Bot's own agent-scope memory, plus the shared_user memory it authored
    # (agent scope lives under the agent partition; shared_user under the
    # owner's). MEMNS/other MEM-prefixed rows are not Memory entities, so filter
    # on entity to revoke only actual memory facts.
    agent_rows = [r for r in store.query(K.agent_pk(store.owner_id, agent_id), sk_prefix="MEM#", limit=500)
                  if r.get("entity") == "Memory"]
    shared_rows = [r for r in store.query(K.user_pk(store.owner_id), sk_prefix="MEM#", limit=500)
                   if r.get("entity") == "Memory" and r.get("scope") == "shared_user"
                   and r.get("author") == agent_id]

    published_before = 0
    revoked = 0
    for row in agent_rows + shared_rows:
        if not memory.is_visible(row):
            continue   # already revoked or expired -- nothing to archive
        published_before += 1
        store.update(row["pk"], row["sk"], memory.revoke())
        revoked += 1

    # Audit LAST, with before/after visible counts so the trail records the
    # size of what was archived. Written after the revokes so a partial failure
    # cannot leave a trail claiming more was archived than actually was.
    org_id = _admin_org_id(event)
    audit = govern.admin_audit_event(
        org_id, "agent.memory_archived", actor,
        correlation_id=_correlation_id(event),
        before={"published": published_before},
        after={"published": published_before - revoked},
        detail=_with_reason(f"archived {revoked} memory rows for {agent_id}", body))
    store.put(audit)
    return _resp(200, {"agentId": agent_id, "revoked": revoked,
                       "publishedBefore": published_before,
                       "correlationId": audit["correlationId"]})


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
            agent = store.try_get(K.agent_pk(store.owner_id, existing_id), "META")
            if agent:
                return _resp(200, agent)

    active = [r for r in store.query_index("gsi1", "gsi1pk", "AGENTS", limit=200)
              if r.get("status", r.get("state")) in A.SEATED]

    # A Bot the owner creates starts with every app already connected: connecting
    # means their Bots can use it, and one made afterwards should not be the
    # exception. A request that names grants (even an empty list) is taken as
    # written, and a Bot that another Bot proposes never comes through here.
    if "grants" not in body:
        body = {**body, "grants": C.default_grants(store)}

    # Started under someone other than Chief? Then that Bot has to exist. Left out,
    # it reports to Chief, which needs nothing stored.
    if body.get("reportsTo"):
        body = {**body, "reportsTo": org.validate(
            "", body["reportsTo"], store.query_index("gsi1", "gsi1pk", "AGENTS", limit=200))}

    plan = A.plan_create(
        body, actor,
        org_connectors=_org_connectors(store),
        active_count=len(active),
        max_agents=int(os.environ.get("MAX_AGENTS", A.DEFAULT_MAX_AGENTS)),
        has_entrypoint=any(r.get("entrypoint") for r in active),
    )

    # A browser creates a Bot by tier, never by guessing a Bedrock identifier.
    # Reuse an already-provisioned seat's resolved model when one exists in the
    # org; it was discovered from this account by resolve_models.py, so a first
    # Bot can be created without turning the UI into a model-ID configuration
    # screen. A brand-new self-serve signup's org is empty, so this falls back
    # to the platform-wide model registry (the cross-owner cache of what this
    # account's tiers resolved to) inside `resolve_model_id`. Still no guess: a
    # fresh account with nothing recorded fails clearly at provisioning time.
    provisioning.resolve_model_id(plan.agent, active)

    if store.try_get(K.agent_pk(store.owner_id, plan.agent_id), "META"):
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
    """Give the agent its runtime identity and mark it runnable (`provisioning`)."""
    return provisioning.provision_harness(store, agent)


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

    * **Who wakes.** A room starts every member in parallel for a task message;
      `@` mentions are the one way to deliberately address a subset. A direct
      thread wakes its one Bot.
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

    thread = store.get(K.thread_pk(store.owner_id, thread_id), "META")
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
        "pk": K.thread_pk(store.owner_id, thread_id), "sk": K.message_sk(now_iso(), ordered_suffix()),
        "entity": "Message", "role": "user", "author": "you", "text": text,
        **({"skill": skill["name"]} if skill else {}),
    })
    store.update(K.thread_pk(store.owner_id, thread_id), "META", threads.touch(text, "user"))

    names = {}
    if len(targets) > 1:
        for a in targets:
            row = store.try_get(K.agent_pk(store.owner_id, a), "META")
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
    for i, agent_id in enumerate(targets):
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
            # Spreads this room wake's InvokeHarness calls out instead of
            # firing all of them at the one shared harness in the same
            # instant -- see WAKE_STAGGER_SECONDS in orchestrator.py.
            trigger["wakeIndex"] = i
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
        if store.try_get(K.thread_pk(store.owner_id, thread_id), "META"):
            threads.event(store, thread_id, text, icon=icon, **extra)
    except Exception:  # noqa: BLE001
        traceback.print_exc()


def _patch_room(store: Store, thread_id: str, body: dict):
    """Change a room's title or who is in it.

    The cap (`collab.MAX_ROOM_MEMBERS`) and the "must be a real, seated agent"
    check are the same ones creating a room applies -- membership is not a way
    round either.
    """
    thread = store.get(K.thread_pk(store.owner_id, thread_id), "META")
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
        before = thread.get("agentIds") or []
        # A room made while the cap was higher keeps what it has -- it can lose
        # a member or swap one for another -- but it cannot grow past the cap.
        if len(ids) > max(collab.MAX_ROOM_MEMBERS, len(set(before))):
            raise A.ValidationError(f"a room holds at most {collab.MAX_ROOM_MEMBERS} agents")
        for agent_id in ids:
            row = store.try_get(K.agent_pk(store.owner_id, agent_id), "META")
            if not row or row.get("status", row.get("state")) not in A.SEATED:
                raise A.ValidationError(f"no such agent {agent_id!r}")
        added = [a for a in ids if a not in before]
        removed = [a for a in before if a not in ids]
        changes["agentIds"] = ids

    if not changes:
        raise A.ValidationError("no editable fields supplied")

    updated = store.update(K.thread_pk(store.owner_id, thread_id), "META", changes)

    def name(agent_id: str) -> str:
        return (store.try_get(K.agent_pk(store.owner_id, agent_id), "META") or {}).get("name", agent_id)

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
    """Raw shell in one logical Bot's isolated runtime session.

    Direct chats have one unambiguous Bot. A room may have several Bots on the
    same account harness, so selecting its first member silently would put a
    command in the wrong microVM; callers must name the member there.
    """
    command = (body.get("command") or "").strip()
    if not command:
        return _resp(400, {"error": "command is required"})

    thread = store.get(K.thread_pk(store.owner_id, thread_id), "META")
    agent_ids = thread.get("agentIds") or []
    if not agent_ids:
        return _resp(400, {"error": "no agent assigned to this thread"})
    requested = (body.get("agentId") or "").strip()
    if thread.get("kind") == "room" and not requested:
        return _resp(400, {"error": "agentId is required for a group-chat computer"})
    agent_id = requested or agent_ids[0]
    if agent_id not in agent_ids:
        return _resp(403, {"error": "that Bot is not a member of this thread"})
    agent = store.get(K.agent_pk(store.owner_id, agent_id), "META")

    core = agentcore.AgentCore()
    harness_arn, session_id = standard_runtime.for_exec(store, agent, client=core)
    # A direct thread and its model runs derive this same v2 key. For a room,
    # shell access is explicit but still belongs to the selected Bot's room
    # session, not its DM. The current console exposes Computer from DMs only;
    # this branch keeps the API correct before a room UI is added.
    if thread_id != f"dm-{agent_id}":
        session_id = K.bot_session_id(store.owner_id, agent_id, thread_id)

    result = core.exec(
        harness_arn=harness_arn,
        session_id=session_id,
        command=command,
    )
    return _resp(200, {
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "exitCode": result.get("exitCode", 0),
        "agentId": agent_id,
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
    creation_error = ""
    creation_warning = ""
    first_task = None
    if approve and decided["action"] == "agent.create":
        try:
            created = _create_approved_agent(store, decided["arguments"], actor)
        except Exception as exc:  # creation itself failed; resume the proposer with the fact
            creation_error = f"{type(exc).__name__}: {str(exc)[:500]}"
            try:
                store.update(run_pk, K.approval_sk(approval_id), {
                    "executionStatus": "failed", "executionError": creation_error,
                })
            except Exception as record_exc:  # the parent still must not remain parked
                creation_warning = f"could not record creation failure ({type(record_exc).__name__})"
        else:
            # Persist creation before attempting the first wake. From this
            # point a launch failure cannot leave an approved card claiming
            # nothing happened or strand the parent run on an already-spent
            # decision.
            try:
                store.update(run_pk, K.approval_sk(approval_id), {
                    "createdAgentId": created["agentId"],
                    "executionStatus": "created",
                })
            except Exception as record_exc:
                creation_warning = f"could not annotate created Bot ({type(record_exc).__name__})"

            try:
                first_task = _launch_approved_first_task(
                    store, created, decided["arguments"])
            except Exception as launch_exc:  # no launch bug may relabel a real Bot as failed
                first_task = {
                    "status": "deferred",
                    "reason": f"first-task launch failed ({type(launch_exc).__name__})",
                }
            try:
                store.update(run_pk, K.approval_sk(approval_id), {
                    "firstTaskStatus": first_task,
                })
            except Exception as record_exc:
                creation_warning = (creation_warning + "; " if creation_warning else "") + \
                    f"could not record first-task status ({type(record_exc).__name__})"
            if first_task.get("runId"):
                created = {**created, "firstTaskRunId": first_task["runId"]}
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
    if creation_error:
        note += f" The Bot could not be created: {creation_error}. Nothing was created."
    elif first_task and first_task.get("status") == "deferred":
        note += (" The Bot was created and its first task is visible, but it was not "
                 f"started yet: {first_task.get('reason', 'recipient unavailable')}.")
    elif first_task and first_task.get("status") == "queued":
        note += (" The Bot was created and its first task was queued. The immediate wake "
                 "failed, so the sweeper will retry it.")

    try:
        if decided["action"] == "agent.create":
            if creation_error:
                threads.event(store, decided["threadId"],
                              f"Bot creation failed: {creation_error}", icon="x")
            elif created:
                launch = (first_task or {}).get("status")
                if launch == "started":
                    event_text = f"Created {created['name']} and started its first task"
                elif launch == "queued":
                    event_text = f"Created {created['name']}; first task queued for retry"
                elif launch == "deferred":
                    event_text = (f"Created {created['name']}; first task waiting: "
                                  f"{first_task.get('reason', 'recipient unavailable')}")
                else:
                    event_text = f"Created {created['name']}"
                threads.event(store, decided["threadId"], event_text, icon="check")
        elif created:
            # Written by the approval that did it: the transcript only ever says a
            # skill was saved or a fact shared after it happened.
            if decided["action"] == "skill.create":
                threads.event(store, decided["threadId"], f"Saved as a skill: {created['name']}", icon="file")
            elif decided["action"] == "memory.publish":
                threads.event(store, decided["threadId"], f"Shared with every Bot: {_label(created)}", icon="layers")
    except Exception as event_exc:  # history is observability, never a liveness gate
        creation_warning = (creation_warning + "; " if creation_warning else "") + \
            f"could not write execution history ({type(event_exc).__name__})"

    runs.advance(store, run, RunState.EXECUTING, pending=None)
    _invoke_orchestrator(run_id, store.owner_id, resume=True, resume_note=note,
                         resume_approval=decided)
    fresh_decision = store.get(run_pk, K.approval_sk(approval_id), consistent=True)
    result = {"approval": approvals.to_card(fresh_decision), "resumed": True}
    if creation_error:
        result["creationError"] = creation_error
    if creation_warning:
        result["creationWarning"] = creation_warning
    if first_task:
        result["firstTask"] = first_task
    if created and decided["action"] == "agent.create":
        result["createdAgent"] = created
    elif created and decided["action"] == "skill.create":
        result["createdSkill"] = created
    elif created and decided["action"] == "memory.publish":
        result["createdMemory"] = created
    return _resp(200, result)


def _decide_handoff(store: Store, run_id: str, handoff_id: str, body: dict,
                    actor: A.Actor):
    """A human's accept/reject on a handoff still `proposed` after the
    orchestrator's own auto-accept attempt already declined it. Mirrors
    `_decide`'s shape -- a conditional decision, then a wake -- but the
    accept path is `handoffs.accept`, shared with the automatic caller so a
    human's "accept" and the system's produce identically-shaped state.
    """
    run_pk = K.run_pk(run_id)
    run = store.get(run_pk, "META")
    handoff = store.try_get(run_pk, K.handoff_sk(handoff_id))
    if handoff is None:
        return _resp(404, {"error": "no such handoff"})
    if handoff.get("status") != "proposed":
        return _resp(409, {"error": "handoff already decided", "status": handoff["status"]})

    approve = bool(body.get("approve"))
    decided_by = f"user:{actor.user_id}"
    try:
        if approve:
            accepted = handoffs.accept(store, run, handoff, decided_by=decided_by)
            _invoke_orchestrator(accepted["child"]["runId"], store.owner_id)
            result = {"handoff": accepted["handoff"], "childRunId": accepted["child"]["runId"]}
        else:
            decided = handoffs.reject(store, run, handoff, decided_by=decided_by,
                                      note=body.get("note", ""))
            result = {"handoff": decided}
    except Conflict:
        return _resp(409, {"error": "handoff already decided"})
    except (handoffs.HandoffError, collab.MessagingError) as exc:
        return _resp(422, {"error": str(exc)})

    threads.event(store, run["threadId"],
                  f'{"Accepted" if approve else "Declined"} the handoff to '
                  f'{(store.try_get(K.agent_pk(store.owner_id, handoff["toAgentId"]), "META") or {}).get("name", handoff["toAgentId"])}',
                  icon="check" if approve else "x")
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

    What the new agent starts with is decided *here*, not by the proposal: the
    apps its owner has connected. Approving is the owner's own act, so the agent
    is usable straight away instead of being an empty seat that needs a second
    round of setup. It inherits nothing from the agent that proposed it -- a
    parent limited to read-only does not pass that on, and does not pass on more
    than the owner holds either -- and writes still ask.
    """
    active = [row for row in store.query_index("gsi1", "gsi1pk", "AGENTS", limit=200)
              if row.get("status", row.get("state")) in A.SEATED]
    proposal = {**proposal, "grants": C.default_grants(store)}
    task = provisioning.first_task(proposal.get("firstTask"))
    proposer_id = proposal.get("parentAgentId") or proposal.get("proposedBy") or ""
    proposer = store.try_get(K.agent_pk(store.owner_id, proposer_id), "META") if proposer_id else None
    briefing = ({
        "text": task,
        "author": (proposer or {}).get("name") or proposer_id or "a teammate",
        "fromAgentId": proposer_id,
    } if task else None)
    plan = A.plan_create(
        proposal, actor,
        org_connectors=_org_connectors(store),
        active_count=len(active),
        max_agents=int(os.environ.get("MAX_AGENTS", A.DEFAULT_MAX_AGENTS)),
        initial_briefing=briefing,
    )
    if store.try_get(K.agent_pk(store.owner_id, plan.agent_id), "META"):
        raise Conflict(f"agent {plan.agent_id!r} already exists")

    # Without this an approved proposal reached provisioning with no model and was
    # refused: a person's create reuses the account's resolved model, and so does this.
    provisioning.resolve_model_id(plan.agent, active)

    store.transact_put(plan.items)
    try:
        return _provision_harness(store, plan.agent)
    except Exception:
        store.transact_delete(plan.rollback_keys)
        store.put(A.audit_event(plan.agent_id, "agent.provision_failed", actor,
                                detail="approved agent creation could not provision"))
        raise


def _launch_approved_first_task(store: Store, created: dict, proposal: dict) -> dict:
    """Best-effort launch after durable approved creation.

    The exact bound task is already the Bot's first durable briefing. Creation
    succeeds independently of the wake: budget/concurrency can defer it, queue
    creation can fail cleanly, and a Lambda invoke failure leaves a QUEUED run
    the sweeper can recover. No exception here may strand the proposing run on
    an approval that was already consumed.
    """
    try:
        task = provisioning.first_task(proposal.get("firstTask"))
    except A.ValidationError as exc:
        return {"status": "deferred", "reason": str(exc)}
    if not task:
        return {"status": "not_requested"}

    ok, reason = collab.may_wake_now(store, created, collab.limits_for_org(store))
    if not ok:
        return {"status": "deferred", "reason": reason}

    proposer_id = proposal.get("parentAgentId") or proposal.get("proposedBy") or ""
    try:
        run = runs.create(
            store, agent_id=created["agentId"],
            thread_id=f"dm-{created['agentId']}", goal=task,
            trigger={"type": "agent", "fromAgentId": proposer_id,
                     "brief": True, "approved": True},
        )
    except Exception as exc:  # no run exists; task remains visible for a later retry
        return {"status": "deferred",
                "reason": f"could not queue the first task ({type(exc).__name__})"}

    try:
        _invoke_orchestrator(run["runId"], store.owner_id)
    except Exception as exc:  # the durable QUEUED run is recoverable by the sweeper
        return {"status": "queued", "runId": run["runId"],
                "reason": f"immediate wake failed ({type(exc).__name__})"}
    return {"status": "started", "runId": run["runId"]}


def _invoke_orchestrator(run_id: str, owner_id: str, *, resume: bool = False,
                         resume_note: str = "", resume_approval: dict | None = None,
                         cancel: bool = False) -> None:
    fn = os.environ.get("ORCHESTRATOR_FN_ARN")
    if not fn:
        return
    payload = {"runId": run_id, "ownerId": owner_id}
    if resume:
        payload.update({"resume": True, "resumeNote": resume_note})
        if resume_approval:
            payload["resumeApproval"] = resume_approval
    if cancel:
        # Settle a paused run that was stopped: nothing is watching it.
        payload["cancel"] = True
    boto3.client("lambda").invoke(
        FunctionName=fn, InvocationType="Event",
        Payload=json.dumps(payload).encode(),
    )


def _warm_account_harness(context, owner_id: str) -> None:
    """Start this owner's shared AgentCore harness the moment Auth0 creates
    their first User row, not when they finish the onboarding screens.

    `standard_runtime.ensure_shared_harness` can take longer than fits
    comfortably in the request that triggers it; firing it here, fire-and-
    forget, on a fresh 30-second budget of its own, means Chief's own
    creation later (`_create_agent`, via Onboarding.jsx) usually finds the
    harness already READY instead of waiting on it inline. It is never the
    only path there -- `ensure_shared_harness`'s own claim protocol is built
    for exactly this: two callers for the same owner's harness, one of them
    a head start.

    `context` is the real Lambda context outside tests and `None` inside
    most of them; either way, no `invoked_function_arn` means do nothing
    rather than guess a function name.
    """
    fn = getattr(context, "invoked_function_arn", None)
    if not fn:
        return
    try:
        boto3.client("lambda").invoke(
            FunctionName=fn, InvocationType="Event",
            Payload=json.dumps({"provisionOwner": owner_id}).encode(),
        )
    except Exception:  # noqa: BLE001 -- a signup must never fail because this did
        traceback.print_exc()


def _provision_owner_harness(owner_id: str) -> dict:
    """The async-invoked half of `_warm_account_harness`."""
    store = Store(owner_id)
    try:
        harness_arn = standard_runtime.ensure_shared_harness(store)
        return {"ok": True, "harnessArn": harness_arn}
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _billing_webhook(event: dict):
    """Verify and dispatch one Stripe webhook event.

    Always 200s once the signature verifies, even when `handle_webhook_event`
    reports `handled: False` -- an event type this integration does not act
    on, or one whose customer cannot yet be resolved, is not a delivery
    failure, and a non-2xx response here only makes Stripe retry a request
    that would fail the same way every time. Only a genuinely bad signature,
    or the secret not being configured yet, is refused.
    """
    import base64

    raw = event.get("body") or ""
    payload = base64.b64decode(raw) if event.get("isBase64Encoded") else raw.encode()
    sig_header = _header(event, "stripe-signature")

    try:
        webhook_secret = secrets.stripe_webhook_secret()
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return _resp(503, {"error": "stripe_not_configured", "detail": str(exc)})

    try:
        verified = stripe_client.verify_webhook(payload, sig_header, webhook_secret)
    except stripe_client.SignatureVerificationError as exc:
        return _resp(400, {"error": "invalid_signature", "detail": str(exc)})

    try:
        result = billing.handle_webhook_event(verified)
    except Exception as exc:  # noqa: BLE001
        # A processing failure IS worth a retry -- unlike an unhandled event
        # type, this is Stripe's own at-least-once delivery doing its job.
        traceback.print_exc()
        return _resp(500, {"error": type(exc).__name__, "detail": str(exc)})
    return _resp(200, result)
