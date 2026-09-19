"""Connectors: Pipedream apps, expressed as things AmazAI already understands.

A Pipedream app becomes an AmazAI connector. Nothing about that changes how
permission works:

    catalog  ->  org install  ->  per-agent grant  ->  router.resolve_tools
                                                        -> schema the model sees

Each arrow narrows. A tool that does not survive all four is absent from the
schema, and an absent tool cannot be argued for, injected into, or retried
into existence. Pipedream sits at the far end of that pipeline as a way to
*reach* an API, never as a way to decide whether it may be reached.

Three properties are load-bearing:

**The catalog is an allowlist, not a mirror.** Pipedream offers thousands of
apps and every endpoint each one has. This module exposes a hand-written set
of actions, each pinned to one upstream URL with a declared capability. A
connector the catalog does not describe cannot be installed, so the breadth
of the Pipedream catalog is never the breadth of what an agent can do.

**Capability is declared here, at write time.** `policy.evaluate` turns it
into an approval decision. `slack.post` is already on the always-approve
floor, so a granted, installed, healthy Slack connector still stops for a
human before it posts — which is the intended shape, not an oversight.

**Revocation is immediate because it is subtractive.** Removing an org
install or an agent grant removes rows. The next run resolves its schema from
what is there, so the tools are gone from the next schema built, with no
cache to invalidate and no running agent to notify.
"""

from __future__ import annotations

from dataclasses import dataclass

from amazai import keys as K, router
from amazai.policy import Capability
from amazai.store import Store, now_iso, ordered_suffix

#: Connector ids are namespaced by provider so a future direct integration
#: and a Pipedream-backed one can coexist without colliding.
PREFIX = "pipedream:"


@dataclass(frozen=True)
class Action:
    """One callable thing, pinned to one endpoint.

    `target` is fixed here rather than assembled from tool arguments. The
    model controls arguments; if it also controlled the URL, a grant for
    chat.postMessage would be a grant for every Slack endpoint.
    """
    tool: str
    capability: Capability
    target: str
    method: str
    summary: str
    #: Argument names forwarded upstream. Anything else the model supplies is
    #: dropped rather than passed through.
    arguments: tuple[str, ...]


@dataclass(frozen=True)
class ConnectorSpec:
    app: str                      # Pipedream app slug
    name: str
    description: str
    allowed_prefixes: tuple[str, ...]
    actions: tuple[Action, ...]

    @property
    def connector_id(self) -> str:
        return f"{PREFIX}{self.app}"

    @property
    def tools(self) -> tuple[str, ...]:
        return tuple(a.tool for a in self.actions)

    def action(self, tool: str) -> Action | None:
        for a in self.actions:
            if a.tool == tool:
                return a
        return None

    #: The highest capability any action carries. An org install is authorized
    #: at this level, and a per-agent grant may sit at or below it.
    @property
    def capability(self) -> Capability:
        order = [Capability.READ, Capability.WRITE, Capability.COST,
                 Capability.DESTRUCTIVE, Capability.ADMIN]
        return max((a.capability for a in self.actions),
                   key=order.index, default=Capability.READ)


#: The proof-of-concept catalog: one app, two actions, deliberately.
#:
#: Slack is the right first connector because its two actions sit on opposite
#: sides of the approval boundary. `slack.read` is READ and flows without a
#: decision; `slack.post` is on the always-approve floor and cannot be
#: pre-approved away. One connector therefore proves both halves of the
#: enforcement path end to end.
CATALOG: dict[str, ConnectorSpec] = {
    f"{PREFIX}slack": ConnectorSpec(
        app="slack",
        name="Slack",
        description="Read recent channel history, and post messages with approval.",
        allowed_prefixes=("https://slack.com/api/",),
        actions=(
            Action(
                tool="slack.read",
                capability=Capability.READ,
                target="https://slack.com/api/conversations.history",
                method="GET",
                summary="Read recent messages in a channel",
                arguments=("channel", "limit"),
            ),
            Action(
                tool="slack.post",
                capability=Capability.WRITE,
                target="https://slack.com/api/chat.postMessage",
                method="POST",
                summary="Post a message to a channel",
                arguments=("channel", "text"),
            ),
        ),
    ),
}


class UnknownConnector(KeyError):
    pass


class NotInstalled(PermissionError):
    """The organization does not hold this connector."""


class NotGranted(PermissionError):
    """This agent was not granted this tool."""


def spec(connector_id: str) -> ConnectorSpec:
    try:
        return CATALOG[connector_id]
    except KeyError as exc:
        raise UnknownConnector(
            f"{connector_id!r} is not in the connector catalog"
        ) from exc


def catalog_for_console() -> list[dict]:
    """What is installable, for the console's connector picker."""
    return [{
        "connectorId": s.connector_id,
        "app": s.app,
        "name": s.name,
        "description": s.description,
        "capability": s.capability.value,
        "actions": [{"tool": a.tool, "capability": a.capability.value,
                     "summary": a.summary} for a in s.actions],
    } for s in CATALOG.values()]


# --- org install ------------------------------------------------------------

def install(store: Store, connector_id: str, *, account_id: str,
            external_user_id: str, actor_user_id: str,
            capability: Capability | None = None,
            allowed_tools: list[str] | None = None) -> dict:
    """Record that the organization holds this connector.

    `account_id` is a Pipedream reference (`apn_...`), not a credential. The
    third party's token stays on Pipedream's side and is injected by the
    proxy at call time, so nothing secret is written here — which is what
    makes this row safe to read back into the console.
    """
    s = spec(connector_id)

    # Sorted either way, so the stored row does not depend on which branch
    # produced it.
    tools = sorted(set(allowed_tools) & set(s.tools)) if allowed_tools else sorted(s.tools)
    if not tools:
        raise NotInstalled(f"{connector_id}: no catalog tools selected")

    level = capability or s.capability

    return store.put({
        "pk": K.connector_pk(connector_id), "sk": "META",
        "entity": "Connector", "connectorId": connector_id,
        "gsi1pk": "CONNECTORS", "gsi1sk": s.name,
        "app": s.app, "name": s.name,
        "status": "installed",
        "capability": level.value,
        "allowedTools": tools,
        "accountId": account_id,
        "externalUserId": external_user_id,
        "installedBy": actor_user_id,
        "installedAt": now_iso(),
    })


def revoke(store: Store, connector_id: str) -> dict:
    """Remove the org install and every agent grant that depended on it.

    Subtractive on purpose. The next run builds its schema from the rows that
    remain, so the tools are gone from the next schema built — there is no
    cache to invalidate and no running agent to notify.
    """
    removed_from: list[str] = []
    for agent in store.query_index("gsi1", "gsi1pk", "AGENTS", limit=200):
        grant = store.try_get(K.agent_pk(agent["agentId"]),
                              K.grant_sk(connector_id))
        if grant:
            store.delete(K.agent_pk(agent["agentId"]), K.grant_sk(connector_id))
            removed_from.append(agent["agentId"])

    store.delete(K.connector_pk(connector_id), "META")
    return {"connectorId": connector_id, "revokedFrom": removed_from}


def installed(store: Store) -> dict[str, dict]:
    return {r["connectorId"]: r
            for r in store.query_index("gsi1", "gsi1pk", "CONNECTORS", limit=200)
            if r.get("status") == "installed"}


# --- resolution -------------------------------------------------------------

def router_grants(store: Store, agent_id: str) -> list[router.Grant]:
    """The agent's connector grants, as the router wants them.

    Intersected with the org install every time rather than trusted as
    written. A grant row that outlived its install — a revoke that raced a
    read, a restored backup — contributes nothing, because the intersection
    is empty.
    """
    org = installed(store)
    out: list[router.Grant] = []

    for grant in store.query(K.agent_pk(agent_id), sk_prefix="GRANT#"):
        connector_id = grant.get("connectorId")
        org_row = org.get(connector_id)
        if not org_row:
            continue
        if connector_id not in CATALOG:
            continue

        tools = (set(grant.get("allowedTools") or [])
                 & set(org_row.get("allowedTools") or [])
                 & set(CATALOG[connector_id].tools))
        if not tools:
            continue

        out.append(router.Grant(
            connector_id=connector_id,
            allowed_tools=frozenset(tools),
            capability=grant.get("capability", "read"),
        ))
    return out


def action_for(tool: str, grants: list[router.Grant]) -> tuple[ConnectorSpec, Action]:
    """Find the catalog action behind a tool the agent actually holds.

    Raises rather than returning None: reaching here with an ungranted tool
    means resolution let something through, and that is a boundary failure,
    not a miss.
    """
    for grant in grants:
        if tool in grant.allowed_tools:
            s = CATALOG.get(grant.connector_id)
            action = s.action(tool) if s else None
            if action:
                return s, action
    raise NotGranted(f"{tool} is not granted to this agent")


# --- invocation -------------------------------------------------------------

def connector_event(connector_id: str, action: str, *, agent_id: str = "",
                    run_id: str = "", actor_user_id: str = "",
                    outcome: str = "ok", detail: str = "") -> dict:
    """An append-only connector log line.

    Installation, authorization, invocation, revocation and failure all land
    here, under the connector's own partition, so "what has this connector
    done" is one query rather than a join across runs.
    """
    stamp = now_iso()
    return {
        "pk": K.connector_pk(connector_id),
        "sk": f"LOG#{stamp}#{ordered_suffix()}",
        "entity": "ConnectorEvent",
        "gsi1pk": "CONNECTORLOG", "gsi1sk": f"{stamp}#{connector_id}",
        "connectorId": connector_id, "action": action, "at": stamp,
        "agentId": agent_id, "runId": run_id, "actorUserId": actor_user_id,
        "outcome": outcome, "detail": detail,
    }


def invoke(store: Store, client, *, agent_id: str, tool: str, arguments: dict,
           grants: list[router.Grant], run_id: str = "") -> dict:
    """Call a granted connector tool through the Pipedream proxy.

    By the time this runs, three gates have already passed: the org installed
    the connector, the agent was granted the tool, and `policy.evaluate`
    either found it read-only or obtained an approval. This function does not
    re-decide any of that — it checks that the tool is still granted, drops
    any argument the catalog does not name, and makes the call.
    """
    s, action = action_for(tool, grants)
    org = installed(store).get(s.connector_id)
    if not org:
        raise NotInstalled(f"{s.connector_id} is not installed")

    # Only the arguments the catalog names travel upstream. A model that adds
    # a field is adding it to a request it does not get to shape.
    body = {k: v for k, v in (arguments or {}).items() if k in action.arguments}

    try:
        result = client.proxy(
            external_user_id=org["externalUserId"],
            account_id=org["accountId"],
            target_url=action.target,
            method=action.method,
            body=body,
            allowed_prefixes=s.allowed_prefixes,
        )
    except Exception as exc:  # noqa: BLE001
        store.put(connector_event(
            s.connector_id, "connector.invocation_failed", agent_id=agent_id,
            run_id=run_id, outcome="error",
            detail=f"{tool}: {type(exc).__name__}: {exc}"))
        raise

    store.put(connector_event(
        s.connector_id, "connector.invoked", agent_id=agent_id, run_id=run_id,
        detail=f"{tool} -> {action.method} {action.target}"))
    return result
