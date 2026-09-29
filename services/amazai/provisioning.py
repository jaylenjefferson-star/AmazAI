"""Making a Bot real: its harness, and creating one on someone's behalf.

Two callers need this and neither may import the other's handler: the API (a
person creates a Bot, or approves a proposal) and the orchestrator (a Bot creates
one because the operator asked it to). So the parts they share live here.

**Creating a Bot at the operator's request** (`create_child`) is the one place an
agent brings a new agent into being, and it is deliberately narrow:

* The caller decides *whether* it is allowed -- only from a run the operator's own
  message started -- and passes `on_owners_request` to `agents.plan_create`, which
  otherwise refuses any agent actor. Nothing the model writes can set that.
* The child never holds more than its creator does. Its apps are the creator's,
  each at the creator's own ceiling, and its optional tools are a subset of the
  creator's. A read-only Bot cannot make a write-capable one.
* The child reports to its creator, is recorded as its creator's, and carries the
  same budget any Bot the operator makes carries. There is no cap on how many a Bot
  may create; the only ceiling is the organisation's own (`agents.DEFAULT_MAX_AGENTS`).
* Every one is audited as `agent.created` with the creating Bot named.
"""

from __future__ import annotations

import os

from amazai import agents as A, connectors, keys as K, platform_models, standard_runtime
from amazai.policy import Capability
from amazai.store import Store

#: Optional built-ins a Bot may ask for. The terminal and the Bot's own files are always on.
OPTIONAL_TOOLS = frozenset({"browser", "code_interpreter"})
FIRST_TASK_MAX = 4000


class ChildCreationError(Exception):
    """A Bot could not be created. The message is written for the model to read."""


def org_connectors(store: Store) -> dict[str, A.OrgConnector]:
    """Connectors the organization has installed, keyed by id.

    The ceiling for every per-agent grant. Empty until a connector is
    installed, which is why a grant request on a fresh org is refused rather
    than quietly granted -- there is nothing yet to grant from.
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


def seated_agents(store: Store) -> list[dict]:
    """Bots that count against the organisation's ceiling."""
    return [r for r in store.query_index("gsi1", "gsi1pk", "AGENTS", limit=200)
            if r.get("status", r.get("state")) in A.SEATED]


def max_agents() -> int:
    return int(os.environ.get("MAX_AGENTS", A.DEFAULT_MAX_AGENTS))


def resolve_model_id(agent: dict, seated: list[dict], *, table=None) -> None:
    """Give a new Bot the model an existing one already resolved, in place.

    A Bot is created by *tier*, never by a guessed Bedrock identifier. The account's
    resolved model is reused from a Bot that has one. Both creation paths need this:
    without it a Bot approved from a proposal reached provisioning with no model and
    was refused.

    The seated-Bot reuse is scoped to *this owner's* org, which is empty for a
    brand-new self-serve signup -- so it falls back to the platform-wide model
    registry (`platform_models`), the cross-owner cache of what this AWS account's
    tiers resolved to. Still never a guess: the registry only ever holds ids that
    were resolved from the live account, so a tier with nothing recorded leaves
    `modelId` None and fails at provisioning exactly as before.
    """
    if (agent.get("model") or {}).get("modelId"):
        return
    resolved = next((r.get("model", {}).get("modelId") for r in seated
                     if r.get("model", {}).get("modelId")), None)
    if not resolved:
        tier = (agent.get("model") or {}).get("tier") or ""
        resolved = platform_models.resolve(tier, table=table)
    if resolved:
        agent["model"]["modelId"] = resolved


def provision_harness(store: Store, agent: dict, *,
                      wait_seconds: float | None = None,
                      ready_wait_seconds: float | None = None,
                      release_on_timeout: bool = True) -> dict:
    """Attach a logical Bot to the owner's standard AgentCore runtime.

    Bot identity and authority live in the control plane and travel on every
    invocation. Standard Bots therefore share one restricted account harness;
    AgentCore gives each `(owner, Bot, thread)` session its own isolated
    microVM. `standard_runtime.provision_bot` retains the former dedicated
    path behind `AMAZAI_SHARED_RUNTIME=false` for rollback.

    Separated so the whole create path can be exercised without an AWS
    account: tests swap this seam and still drive transaction, rollback and
    audit behavior.
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

    provisioned = standard_runtime.provision_bot(
        store, agent, wait_seconds=wait_seconds,
        ready_wait_seconds=ready_wait_seconds,
        release_on_timeout=release_on_timeout,
    )
    # Seed the cross-owner registry from a Bot that just resolved a model, so the
    # next self-serve signup's first Bot has a model to reuse without a
    # resolve_models.py re-run. Best-effort; never fails a successful create.
    platform_models.record_from_agent(agent)
    return provisioned


def first_task(value) -> str:
    """One normalized first assignment, shared by storage and run creation."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if len(text) > FIRST_TASK_MAX:
        raise A.ValidationError(
            f"firstTask must be at most {FIRST_TASK_MAX} characters; "
            f"got {len(text)}. Put durable instructions in description and keep "
            "the first assignment focused")
    return text


def child_body(store: Store, creator: dict, args: dict) -> dict:
    """What a Bot may nominate for a child, and what it inherits from the nominator.

    The model supplies who the child is (name, title, role, standing orders, tier).
    It does not supply what the child may reach: that is the creator's own access,
    read from the store, so it can neither be widened by what the model writes nor
    exceed what the creator holds.
    """
    grants = [{"connectorId": g.connector_id, "capability": g.capability.value,
               "allowedTools": sorted(g.tools)}
              for g in connectors.granted_apps(store, creator["agentId"])]
    held = set(creator.get("allowedTools") or [])
    tools = [t for t in (args.get("tools") or [])
             if isinstance(t, str) and t in OPTIONAL_TOOLS and t in held]
    body = {k: args[k] for k in ("name", "title", "role", "description", "systemPrompt",
                                 "modelTier", "workingStyle", "avatar")
            if args.get(k) not in (None, "")}
    body["tools"] = tools
    body["grants"] = grants
    return body


def create_child(store: Store, creator: dict, args: dict) -> dict:
    """Create a Bot on the operator's request and return its row. Raises ChildCreationError.

    Whether the operator asked is the caller's to establish; this trusts that it did.
    See the module docstring for what it does and does not do.
    """
    actor = A.Actor(user_id=store.owner_id, org_id=creator.get("orgId") or "",
                    agent_id=creator["agentId"])
    seated = seated_agents(store)
    try:
        task = first_task(args.get("firstTask"))
        briefing = ({
            "text": task,
            "author": creator.get("name") or creator["agentId"],
            "fromAgentId": creator["agentId"],
        } if task else None)
        plan = A.plan_create(child_body(store, creator, args), actor,
                             org_connectors=org_connectors(store),
                             active_count=len(seated), max_agents=max_agents(),
                             on_owners_request=True,
                             initial_briefing=briefing)
    except (A.ValidationError, A.QuotaExceeded, A.Conflict, A.Escalation) as exc:
        raise ChildCreationError(str(exc)) from exc

    if store.try_get(K.agent_pk(store.owner_id, plan.agent_id), "META"):
        raise ChildCreationError(
            f"a Bot with the id {plan.agent_id!r} already exists; choose a different name")

    resolve_model_id(plan.agent, seated)
    store.transact_put(plan.items)
    try:
        return provision_harness(store, plan.agent)
    except Exception as exc:  # noqa: BLE001
        store.transact_delete(plan.rollback_keys)
        store.put(A.audit_event(plan.agent_id, "agent.provision_failed", actor,
                                detail=f"{type(exc).__name__}: {exc}"))
        raise ChildCreationError(
            f"its harness could not be set up ({type(exc).__name__}: {str(exc)[:200]}); "
            "nothing was left behind") from exc
