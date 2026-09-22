"""Connectors: Composio apps, expressed as things AmazAI already understands.

    connect  ->  org install  ->  per-Bot grant  ->  connector_search / connector_call
                                                       -> policy.evaluate -> Composio

Composio offers well over a thousand apps. There is no allowlist here: any app a
person can connect is a connector, and connecting it makes it usable by their
Bots. What stays is the gate, and it is per *call*, not per app:

**Every tool is classified when it is called.** `composio.classify` reads the
tool's own behaviour tags and `policy.evaluate` turns the result into a decision
-- reads run, anything that creates, changes or removes data is held for the
operator's approval, and an unlabelled tool counts as a write. Nothing in this
file knows what any particular app's tools do, because it does not need to.

**A Bot's grant is a ceiling, not a list.** The default grant is the whole app.
Narrow it and the narrower rule wins: `capability: read` makes a Bot read-only in
that app whatever the tool is called; an explicit tool list allows only those.

**Revocation is immediate because it is subtractive.** Removing an org install or
a grant removes rows. The next call resolves from what is there, so there is no
cache to invalidate and no running Bot to notify.

The model never sees a per-tool schema for a connector. Tools cannot be declared
one by one for thousands of apps, so it is given two fixed inline tools, and
what `connector_search` returns is limited to the apps its Bot holds a grant for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from amazai import composio, keys as K
from amazai.policy import Capability
from amazai.store import Store, now_iso, ordered_suffix

#: Connector ids are namespaced by provider so a second provider could coexist
#: without colliding, and so rows left by a previous one are recognisably not ours.
PREFIX = "composio:"

#: In a grant's `allowedTools`: every tool of the app, whatever its name.
WILDCARD = "*"

#: The default ceiling for a grant and for an org install. It is "no cap", not
#: "everything is allowed": policy still decides each call from the tool's tags.
CEILING = Capability.ADMIN

_ORDER = {
    Capability.READ: 0, Capability.WRITE: 1, Capability.COST: 2,
    Capability.DESTRUCTIVE: 3, Capability.ADMIN: 4,
}

_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class UnknownConnector(KeyError):
    pass


class NotInstalled(PermissionError):
    """The organization does not hold this connector."""


class NotGranted(PermissionError):
    """This Bot was not granted this tool."""


def connector_id(slug: str) -> str:
    return f"{PREFIX}{slug.lower()}"


def slug_of(cid: str) -> str:
    """The toolkit slug behind a connector id, or UnknownConnector.

    Accepts the bare slug too (`gmail`), since that is what a model or a person
    writes; anything that would not be a safe slug is refused here, before it
    can be used in a key or a URL.
    """
    raw = (cid or "").strip()
    slug = raw[len(PREFIX):] if raw.startswith(PREFIX) else raw
    slug = slug.lower()
    if not _SLUG.match(slug):
        raise UnknownConnector(f"{cid!r} is not a connector")
    return slug


def is_ours(cid: str) -> bool:
    return isinstance(cid, str) and cid.startswith(PREFIX)


@dataclass(frozen=True)
class Granted:
    """One app a Bot may use, and how far."""
    connector_id: str
    slug: str
    tools: frozenset[str]
    capability: Capability

    def allows(self, tool_slug: str) -> bool:
        return WILDCARD in self.tools or tool_slug in self.tools


# --- org install ------------------------------------------------------------

def install(store: Store, cid: str, *, name: str, account_id: str,
            external_user_id: str, actor_user_id: str) -> dict:
    """Record that the organization holds this connector.

    `account_id` is a Composio reference (`ca_...`), not a credential. The
    third party's token stays with Composio and is applied on their side at call
    time, so nothing secret is written here -- which is what makes this row safe
    to read back into the console.
    """
    slug = slug_of(cid)
    cid = connector_id(slug)
    return store.put({
        "pk": K.connector_pk(store.owner_id, cid), "sk": "META",
        "entity": "Connector", "connectorId": cid,
        "gsi1pk": "CONNECTORS", "gsi1sk": name or slug,
        "app": slug, "name": name or slug,
        "status": "installed",
        "capability": CEILING.value,
        "allowedTools": [WILDCARD],
        "accountId": account_id,
        "externalUserId": external_user_id,
        "installedBy": actor_user_id,
        "installedAt": now_iso(),
    })


def grant_row(agent_id: str, cid: str, *, actor_user_id: str,
              capability: Capability = CEILING,
              tools: list[str] | None = None) -> dict:
    return {
        "pk": K.agent_pk(agent_id), "sk": K.grant_sk(cid),
        "entity": "Grant", "agentId": agent_id,
        "grantedBy": actor_user_id, "grantedAt": now_iso(),
        "connectorId": cid, "capability": capability.value,
        "allowedTools": sorted(tools) if tools else [WILDCARD],
    }


def grant_to_active_agents(store: Store, cid: str, *, actor_user_id: str) -> list[str]:
    """Make a freshly connected app usable by every active Bot.

    Only Bots that do not already hold a grant for it: a grant someone narrowed
    to read-only is left exactly as it was, so reconnecting never widens it.
    """
    granted: list[str] = []
    for agent in store.query_index("gsi1", "gsi1pk", "AGENTS", limit=200):
        if agent.get("status") not in (None, "active"):
            continue
        if store.try_get(K.agent_pk(agent["agentId"]), K.grant_sk(cid)):
            continue
        store.put(grant_row(agent["agentId"], cid, actor_user_id=actor_user_id))
        granted.append(agent["agentId"])
    return granted


def default_grants(store: Store) -> list[dict]:
    """What a Bot the owner creates starts with: every app already connected.

    Applied only to a Bot the owner creates. One a Bot proposes still starts with
    no connectors -- an agent cannot mint authority, including for the next one.
    """
    return [{"connectorId": cid, "capability": CEILING.value, "allowedTools": [WILDCARD]}
            for cid in installed(store)]


def revoke(store: Store, cid: str) -> dict:
    """Remove the org install and every agent grant that depended on it."""
    removed_from: list[str] = []
    for agent in store.query_index("gsi1", "gsi1pk", "AGENTS", limit=200):
        if store.try_get(K.agent_pk(agent["agentId"]), K.grant_sk(cid)):
            store.delete(K.agent_pk(agent["agentId"]), K.grant_sk(cid))
            removed_from.append(agent["agentId"])
    store.delete(K.connector_pk(store.owner_id, cid), "META")
    return {"connectorId": cid, "revokedFrom": removed_from}


def installed(store: Store) -> dict[str, dict]:
    """The org's installed connectors. Rows a previous provider left behind are
    not connectors any more and are not returned."""
    return {r["connectorId"]: r
            for r in store.query_index("gsi1", "gsi1pk", "CONNECTORS", limit=200)
            if r.get("status") == "installed" and is_ours(r.get("connectorId", ""))}


# --- resolution -------------------------------------------------------------

def granted_apps(store: Store, agent_id: str) -> list[Granted]:
    """The apps this Bot may use right now.

    Intersected with the org install every time rather than trusted as written:
    a grant that outlived its install -- a revoke that raced a read, a restored
    backup -- contributes nothing, because the intersection is empty.
    """
    org = installed(store)
    out: list[Granted] = []
    for grant in store.query(K.agent_pk(agent_id), sk_prefix="GRANT#"):
        cid = grant.get("connectorId", "")
        if cid not in org:
            continue
        try:
            capability = Capability(grant.get("capability") or CEILING.value)
        except ValueError:
            capability = Capability.READ   # an unreadable ceiling is the low one
        tools = frozenset(grant.get("allowedTools") or [])
        if not tools:
            continue
        out.append(Granted(cid, org[cid]["app"], tools, capability))
    return out


def grant_for(granted: list[Granted], toolkit_slug: str) -> Granted | None:
    slug = (toolkit_slug or "").lower()
    return next((g for g in granted if g.slug == slug), None)


def authorize(granted: list[Granted], *, toolkit_slug: str, tool: str,
              capability: Capability) -> Granted:
    """Check one call against the Bot's grant. Raises NotGranted, never returns None.

    This is the ceiling and the tool list. Whether the call also needs a human is
    `policy.evaluate`'s job and happens after this; a call this refuses never
    gets far enough to ask.
    """
    g = grant_for(granted, toolkit_slug)
    if g is None:
        raise NotGranted(f"{toolkit_slug or 'that app'} is not connected for this Bot")
    if not g.allows(tool):
        raise NotGranted(f"{tool} is not among the tools granted for {g.slug}")
    if _ORDER[capability] > _ORDER[g.capability]:
        raise NotGranted(
            f"{tool} would {_effect(capability)}, but this Bot's access to {g.slug} "
            f"is {g.capability.value}-only")
    return g


def _effect(capability: Capability) -> str:
    return {Capability.READ: "read data", Capability.WRITE: "change data",
            Capability.COST: "spend money", Capability.DESTRUCTIVE: "remove data",
            Capability.ADMIN: "change settings"}.get(capability, capability.value)


# --- invocation -------------------------------------------------------------

def connector_event(owner_id: str, cid: str, action: str, *, agent_id: str = "",
                    run_id: str = "", actor_user_id: str = "",
                    outcome: str = "ok", detail: str = "") -> dict:
    """An append-only connector log line.

    Installation, authorization, invocation, revocation and failure all land
    here, under the connector's own partition, so "what has this connector
    done" is one query rather than a join across runs.
    """
    stamp = now_iso()
    return {
        "pk": K.connector_pk(owner_id, cid),
        "sk": f"LOG#{stamp}#{ordered_suffix()}",
        "entity": "ConnectorEvent",
        "gsi1pk": "CONNECTORLOG", "gsi1sk": f"{stamp}#{cid}",
        "connectorId": cid, "action": action, "at": stamp,
        "agentId": agent_id, "runId": run_id, "actorUserId": actor_user_id,
        "outcome": outcome, "detail": detail,
    }


def invoke(store: Store, client, *, agent_id: str, grant: Granted, tool: str,
           arguments: dict, run_id: str = "") -> dict:
    """Run one tool through Composio.

    By the time this runs the gates have passed: the org installed the app, the
    Bot was granted it (`authorize`), and `policy.evaluate` either found the call
    a read or obtained an approval. This function re-decides none of that. It
    reads the account reference from the install row, so a revoke that landed
    mid-run stops the very next call, and makes the call.
    """
    org = installed(store).get(grant.connector_id)
    if not org:
        raise NotInstalled(f"{grant.connector_id} is not installed")

    try:
        result = client.execute(org["externalUserId"], tool, arguments or {},
                                account_id=org.get("accountId") or None)
    except Exception as exc:  # noqa: BLE001
        store.put(connector_event(
            store.owner_id, grant.connector_id, "connector.invocation_failed", agent_id=agent_id,
            run_id=run_id, outcome="error",
            detail=f"{tool}: {type(exc).__name__}: {str(exc)[:200]}"
                   + (f" (log {exc.log_id})" if getattr(exc, "log_id", "") else "")))
        raise

    store.put(connector_event(
        store.owner_id, grant.connector_id, "connector.invoked", agent_id=agent_id, run_id=run_id,
        detail=f"{tool}" + (f" (log {result.get('logId')})" if result.get("logId") else "")))
    return result
