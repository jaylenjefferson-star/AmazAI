"""Agent lifecycle: create, read, update, archive.

The agent record is the source of truth for the Create-a-Bot interface. The
console renders what is written here; it does not hold a second idea of what
an agent is.

Three rules shape this module, and each of them is a security property rather
than a preference:

1. **Creation is one fact, not several.** An agent's identity, its memory
   namespace, its budget and its opening grant set are written in a single
   transaction. A crash midway used to be able to leave an agent that exists
   but cannot run — visible in the list, missing its grants. `plan_create`
   builds every row up front so the write is atomic, and `rollback_keys`
   exists for the one step that cannot be transactional: the harness.

2. **Nothing grants itself.** An agent may not widen its own access, and an
   actor may not hand out more than the organization already holds. Both are
   checked here, before anything reaches the store — see
   `assert_no_self_escalation` and `validate_grants`.

3. **Deactivation, never deletion.** Archiving keeps the evidence trail
   attached to something. `policy.ALWAYS_APPROVE` already lists `agent.delete`
   for the day a real delete exists; until then there is no code path to one.

No AWS call is made from this module. Provisioning side effects are the
caller's, which is what makes all of this testable without an account.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from amazai import keys as K, models, onboarding, threads
from amazai.policy import Capability, NEVER_APPROVABLE, _matches
from amazai.store import Conflict, now_iso, ordered_suffix

# --- vocabulary -------------------------------------------------------------

#: Lifecycle. `provisioning` and `failed` are internal: an agent in either
#: state is never handed to a model and never counts against a seat.
STATUSES: frozenset[str] = frozenset({
    "provisioning", "active", "paused", "archived", "failed",
})

#: States in which an agent may actually run.
RUNNABLE: frozenset[str] = frozenset({"active"})

#: States that consume a seat. An archived agent keeps its evidence but frees
#: the seat it held, which is the practical reason to archive rather than pause.
SEATED: frozenset[str] = frozenset({"provisioning", "active", "paused"})

#: The companion archetypes, and the same six keys the console's character
#: system draws (`web/src/characters/archetypes.jsx`). They must stay in step:
#: `useAgents.presentAgent` maps a stored `avatar.shape` straight onto an
#: archetype, so a name accepted here that the console cannot draw silently
#: becomes the fallback pebble, and a name the console offers that is refused
#: here makes the create form fail on submit.
#:
#: This was geometry once -- circle, squircle, hex -- from before the
#: characters existed. Only `cloud` overlapped the archetypes, so five of the
#: six characters the picker offered were refused by this validator. It had
#: never been caught because the console has never run against a deployed API.
#: `test_character_parity.py` is the guard.
AVATAR_SHAPES: tuple[str, ...] = (
    "pebble", "paper", "jelly", "cloud", "lantern", "moth",
)

#: Fixed palette. Free-form colour would let two agents be visually
#: indistinguishable, and the avatar is how an agent is recognised in a
#: handoff line where there is no room for its name.
AVATAR_COLORS: tuple[str, ...] = (
    "#e5484d", "#e8833a", "#f0a93b", "#3dc98a", "#12a594",
    "#2f6fe4", "#8b5cf6", "#e93d82", "#8b6c4e", "#8a909c",
)

WORKING_STYLES: tuple[str, ...] = (
    "autonomous",   # acts, then reports
    "collaborative",  # proposes, waits for a nudge
    "advisory",     # never acts; drafts and recommends
)

#: Longest role chip. Sized for a roster row, not a sentence.
TITLE_MAX = 24

NAME_RE = re.compile(r"^[\w][\w \-'&,.()/]{1,59}$")

#: `HH:MM`, 24-hour. Stored as written rather than as minutes since midnight
#: so the value a person typed is the value the console shows back.
CLOCK_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

#: An IANA zone name. Validated by shape, not against a bundled database:
#: the zone is carried to EventBridge Scheduler, which is the thing that
#: actually resolves it, and a stale local copy would refuse zones that AWS
#: accepts. `UTC` is the one single-segment name allowed.
TZ_RE = re.compile(r"^(UTC|[A-Za-z]+(?:_[A-Za-z]+)*(?:/[A-Za-z0-9+_-]+){1,2})$")


AGENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")

#: Ceiling on agents an organization may hold at once. Refused at creation
#: rather than degrading later, because the failure mode of an unbounded agent
#: count is a bill, not an error.
DEFAULT_MAX_AGENTS = 25

#: Per-agent limits a caller may not exceed without an explicit org override.
MAX_CONCURRENT_RUNS_CEILING = 8
MAX_MONTHLY_USD_CEILING = 500.0


class ValidationError(ValueError):
    """Bad input. Surfaces as 400."""


class QuotaExceeded(RuntimeError):
    """The organization is at its limit. Surfaces as 409."""


class Escalation(PermissionError):
    """An attempt to acquire access the actor does not hold. Surfaces as 403."""


# --- actors -----------------------------------------------------------------

@dataclass(frozen=True)
class Actor:
    """Who is making the request.

    `agent_id` is set only when the caller is an agent acting through the
    control plane. A human request leaves it None. The distinction is the
    whole of rule 2: an agent is never allowed to be the actor on a grant or
    budget change, including a change to some *other* agent — an agent that
    can widen a peer can widen itself through that peer.
    """
    user_id: str
    org_id: str
    agent_id: str | None = None

    @property
    def is_agent(self) -> bool:
        return self.agent_id is not None


# --- validation -------------------------------------------------------------

def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def normalize_agent_id(name: str, *, explicit: str | None = None) -> str:
    """Derive a stable, readable id from the display name.

    Readable because it appears in S3 prefixes, IAM role names and handoff
    lines; stable because renaming an agent must not orphan its drive.
    """
    if explicit:
        _require(bool(AGENT_ID_RE.match(explicit)),
                 f"agentId must match {AGENT_ID_RE.pattern}")
        return explicit
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:38]
    _require(len(slug) >= 2, f"cannot derive an agent id from {name!r}")
    return slug


def validate_profile(body: dict) -> dict:
    """Check the human-authored half of an agent and return it normalized."""
    name = (body.get("name") or "").strip()
    _require(bool(NAME_RE.match(name)),
             "name must be 2-60 characters of letters, digits or - ' & , . ( ) /")

    role = (body.get("role") or "").strip()
    _require(2 <= len(role) <= 200, "role must be 2-200 characters")

    description = (body.get("description") or "").strip()
    _require(len(description) <= 2000, "description must be at most 2000 characters")

    # The short label the roster shows beside the name ("Email", "Sales").
    # One line and short enough to sit in a chip; `role` is the sentence.
    title = (body.get("title") or "").strip()
    _require(len(title) <= TITLE_MAX and title.isprintable(),
             f"title must be one line of at most {TITLE_MAX} characters")

    # Whether this is the account's first Bot. Read here so a malformed value
    # is refused with the rest of the profile; *whether it is allowed* is
    # `plan_create`'s question, because it depends on who else exists.
    entrypoint = body.get("entrypoint", False)
    _require(isinstance(entrypoint, bool), "entrypoint must be true or false")

    instructions = (body.get("systemPrompt") or "").strip()
    _require(len(instructions) <= 20000,
             "systemPrompt must be at most 20000 characters")

    tier = body.get("modelTier") or models.DEFAULT_TIER
    _require(tier in models.TIERS,
             f"modelTier must be one of {sorted(models.TIERS)}")

    style = body.get("workingStyle") or "collaborative"
    _require(style in WORKING_STYLES,
             f"workingStyle must be one of {list(WORKING_STYLES)}")

    avatar = body.get("avatar") or {}
    shape = avatar.get("shape") or "pebble"
    color = avatar.get("color") or AVATAR_COLORS[5]
    _require(shape in AVATAR_SHAPES, f"avatar.shape must be one of {list(AVATAR_SHAPES)}")
    _require(color in AVATAR_COLORS, f"avatar.color must be one of {list(AVATAR_COLORS)}")

    return {
        "name": name, "role": role, "description": description,
        "title": title, "entrypoint": entrypoint,
        "systemPrompt": instructions or role,
        "modelTier": tier, "workingStyle": style,
        "avatar": {"shape": shape, "color": color},
    }


def validate_schedule(body: dict) -> dict:
    """The half of an agent that says *when*, rather than what or how.

    Hours are a window a routine may fire in, not a guarantee about a run
    already under way: a run that starts at 16:59 is not killed at 17:00.
    Enforcing that here would make a long task unrunnable near the end of a
    day, which is not what anyone means by working hours.
    """
    out: dict = {}

    if "timezone" in body:
        tz = (body.get("timezone") or "").strip()
        _require(bool(TZ_RE.match(tz)),
                 "timezone must be an IANA zone name such as America/Los_Angeles")
        out["timezone"] = tz

    if "workingHours" in body:
        hours = body.get("workingHours")
        # Explicit null clears the window: an agent with no hours is always
        # available, which is a different statement from 00:00-23:59 and the
        # only way to undo a window once set.
        if hours is None:
            out["workingHours"] = None
        else:
            _require(isinstance(hours, dict), "workingHours must be an object or null")
            start, end = (hours.get("start") or ""), (hours.get("end") or "")
            _require(bool(CLOCK_RE.match(start)), "workingHours.start must be HH:MM")
            _require(bool(CLOCK_RE.match(end)), "workingHours.end must be HH:MM")
            _require(start != end, "workingHours.start and end must differ")
            days = hours.get("days")
            if days is None:
                days = list(range(7))
            _require(isinstance(days, list) and days
                     and all(isinstance(d, int) and 0 <= d <= 6 for d in days),
                     "workingHours.days must be a non-empty list of 0-6, Monday first")
            # A window whose end is before its start crosses midnight, which
            # is a real shift and is stored as written rather than refused.
            out["workingHours"] = {"start": start, "end": end,
                                   "days": sorted(set(days))}

    return out

#: The full-computer requirement flags an agent may declare. Kept in step with
#: `amazai.compute.ComputeRequirements`: absence of any flag (an existing agent
#: with no `compute` block) means all-False, which routes to AgentCore. Naming
#: them here rather than importing keeps this validator free of an AWS-adjacent
#: import and mirrors how the compute package treats absence as all-False.
COMPUTE_FLAGS: tuple[str, ...] = (
    "full_desktop",
    "persistent_dev_env",
    "os_level_app",
    "heavy_local_tooling",
)


def validate_compute(body: dict) -> dict:
    """Normalize the OPTIONAL compute-requirement block into a stored shape.

    Additive and backward-compatible: an agent with no `compute` key resolves to
    all-False, so existing agents keep running on AgentCore untouched. Each flag,
    if supplied, must be a bool; absent flags default to False. The returned
    shape (`{"requirements": {...}}`) is what `compute.ComputeRequirements`
    reads off `agent["compute"]`.
    """
    compute = body.get("compute") or {}
    _require(isinstance(compute, dict), "compute must be an object")
    requirements = compute.get("requirements") or {}
    _require(isinstance(requirements, dict),
             "compute.requirements must be an object")

    flags: dict = {}
    for name in COMPUTE_FLAGS:
        value = requirements.get(name, False)
        _require(isinstance(value, bool),
                 f"compute.requirements.{name} must be true or false")
        flags[name] = value
    return {"requirements": flags}


def validate_limits(body: dict) -> dict:
    """Seat, concurrency and budget limits, clamped to their ceilings.

    Refuses rather than clamps silently: a caller that asked for a $2000
    monthly ceiling and got $500 without being told would discover it as an
    agent stopping mid-task.
    """
    budget = body.get("budget") or {}

    per_run = float(budget.get("perRunUsd", 1.0))
    per_month = float(budget.get("perMonthUsd", 20.0))
    concurrency = int(budget.get("maxConcurrentRuns", 1))
    max_calls = int(budget.get("maxToolCallsPerRun", 40))

    _require(per_run > 0, "budget.perRunUsd must be positive")
    _require(per_month > 0, "budget.perMonthUsd must be positive")
    _require(per_run <= per_month,
             "budget.perRunUsd cannot exceed budget.perMonthUsd")
    _require(per_month <= MAX_MONTHLY_USD_CEILING,
             f"budget.perMonthUsd cannot exceed {MAX_MONTHLY_USD_CEILING}")
    _require(1 <= concurrency <= MAX_CONCURRENT_RUNS_CEILING,
             f"budget.maxConcurrentRuns must be 1-{MAX_CONCURRENT_RUNS_CEILING}")
    _require(1 <= max_calls <= 500, "budget.maxToolCallsPerRun must be 1-500")

    on_ceiling = budget.get("onCeiling", "hard_stop")
    _require(on_ceiling in {"hard_stop", "warn"},
             "budget.onCeiling must be hard_stop or warn")

    return {
        "perRunUsd": per_run, "perMonthUsd": per_month,
        "maxConcurrentRuns": concurrency, "maxToolCallsPerRun": max_calls,
        "onCeiling": on_ceiling,
    }


# --- grants -----------------------------------------------------------------

@dataclass(frozen=True)
class OrgConnector:
    """A connector the organization has installed and authorized.

    This is the ceiling for every per-agent grant. An agent cannot be granted
    a tool the organization never installed, and cannot be granted a
    capability above what the organization authorized — which is what keeps a
    read-only Slack install from becoming a write grant one agent at a time.
    """
    connector_id: str
    allowed_tools: frozenset[str]
    capability: Capability


_CAPABILITY_ORDER = {
    Capability.READ: 0,
    Capability.WRITE: 1,
    Capability.COST: 2,
    Capability.DESTRUCTIVE: 3,
    Capability.ADMIN: 4,
}


def validate_grants(requested: list[dict],
                    org_connectors: dict[str, OrgConnector]) -> list[dict]:
    """Check a requested grant set against what the organization holds.

    Every refusal here is a refusal to write, not a refusal to execute. A tool
    that never reaches the grant row never reaches `router.resolve_tools`, and
    so is absent from the schema the model is shown — which is the only form
    of "denied" this system trusts.
    """
    out: list[dict] = []
    seen: set[str] = set()

    for raw in requested:
        connector_id = (raw.get("connectorId") or "").strip()
        _require(bool(connector_id), "each grant needs a connectorId")
        _require(connector_id not in seen,
                 f"duplicate grant for connector {connector_id!r}")
        seen.add(connector_id)

        installed = org_connectors.get(connector_id)
        if installed is None:
            raise Escalation(
                f"connector {connector_id!r} is not installed for this organization"
            )

        try:
            capability = Capability(raw.get("capability") or installed.capability.value)
        except ValueError as exc:
            raise ValidationError(
                f"unknown capability {raw.get('capability')!r} for {connector_id}"
            ) from exc

        if _CAPABILITY_ORDER[capability] > _CAPABILITY_ORDER[installed.capability]:
            raise Escalation(
                f"{connector_id}: cannot grant {capability.value} when the "
                f"organization holds only {installed.capability.value}"
            )

        tools = frozenset(raw.get("allowedTools") or [])
        _require(bool(tools), f"{connector_id}: allowedTools cannot be empty")

        beyond = tools - installed.allowed_tools
        if beyond:
            raise Escalation(
                f"{connector_id}: not installed for {sorted(beyond)}"
            )

        for tool in sorted(tools):
            if _matches(tool, NEVER_APPROVABLE):
                raise Escalation(f"{tool} can never be granted")

        out.append({
            "connectorId": connector_id,
            "capability": capability.value,
            "allowedTools": sorted(tools),
        })

    return out


#: Fields whose change is a privilege change. Auditable, and never writable by
#: an agent actor.
PRIVILEGED_FIELDS: frozenset[str] = frozenset({
    "budget", "allowedTools", "preapproved", "grants", "status",
    "toolCapabilities", "parentAgentId",
})


def assert_no_self_escalation(actor: Actor, target_agent_id: str,
                              changes: dict) -> None:
    """Refuse an agent widening access — its own or anyone's.

    The indirection matters: blocking only `actor.agent_id == target` would
    leave an agent able to widen a peer and then hand work to it, which is the
    same escalation with one more step. So an agent actor may not touch a
    privileged field on any agent.
    """
    if not actor.is_agent:
        return

    touched = sorted(set(changes) & PRIVILEGED_FIELDS)
    if touched:
        raise Escalation(
            f"agent {actor.agent_id!r} may not change {touched} on "
            f"{target_agent_id!r}; grants and budgets are changed by a person"
        )


def check_quota(active_count: int, *, max_agents: int = DEFAULT_MAX_AGENTS) -> None:
    if active_count >= max_agents:
        raise QuotaExceeded(
            f"organization already holds {active_count} agents "
            f"(limit {max_agents}); archive one first"
        )


# --- audit ------------------------------------------------------------------

AUDITED_ACTIONS = (
    "agent.created", "agent.updated", "agent.grants_changed",
    "agent.budget_changed", "agent.deactivated", "agent.provision_failed",
)


def audit_event(agent_id: str, action: str, actor: Actor, *,
                before: dict | None = None, after: dict | None = None,
                detail: str = "") -> dict:
    """An append-only record of a privilege-relevant change.

    Sorted by time under the agent's own partition so an agent's history
    survives on the same row set as the agent, and is still there after it is
    archived.
    """
    _require(action in AUDITED_ACTIONS, f"unknown audit action {action!r}")
    stamp = now_iso()
    return {
        "pk": K.agent_pk(agent_id),
        "sk": f"AUDIT#{stamp}#{ordered_suffix()}",
        "entity": "AuditEvent",
        "gsi1pk": "AUDIT", "gsi1sk": f"{stamp}#{agent_id}",
        "agentId": agent_id, "action": action, "at": stamp,
        "actorUserId": actor.user_id,
        "actorAgentId": actor.agent_id,
        "orgId": actor.org_id,
        "before": before or {}, "after": after or {},
        "detail": detail,
    }


# --- creation ---------------------------------------------------------------

@dataclass
class CreatePlan:
    """Every row a new agent consists of, plus how to undo them.

    Returned rather than written so the caller can put them in one
    transaction, and so every rule above is testable without a table.
    """
    agent_id: str
    items: list[dict] = field(default_factory=list)
    agent: dict = field(default_factory=dict)

    @property
    def rollback_keys(self) -> list[tuple[str, str]]:
        return [(i["pk"], i["sk"]) for i in self.items]


def plan_create(body: dict, actor: Actor, *,
                org_connectors: dict[str, OrgConnector] | None = None,
                active_count: int = 0,
                max_agents: int = DEFAULT_MAX_AGENTS,
                has_entrypoint: bool = False) -> CreatePlan:
    """Validate a create request and lay out every row it implies.

    Nothing here touches the store. If this returns, the agent is creatable;
    if it raises, nothing was written, because nothing had been.

    `has_entrypoint` is whether the organization already has a first Bot. It is
    a parameter rather than a lookup so the rule below stays testable without
    a table, and so the caller -- which already listed the agents to count them
    -- does not list them twice.
    """
    if actor.is_agent:
        raise Escalation("agents do not create agents; a person does")

    check_quota(active_count, max_agents=max_agents)

    # The first Bot's role, title and prompt are the server's to supply. Done
    # before validation so what is validated is what is stored.
    if body.get("entrypoint") is True:
        body = onboarding.apply_defaults(body)

    profile = validate_profile(body)

    if profile["entrypoint"] and has_entrypoint:
        raise Conflict("this organization already has a first Bot")
    budget = validate_limits(body)
    compute = validate_compute(body)
    grants = validate_grants(body.get("grants") or [], org_connectors or {})

    agent_id = normalize_agent_id(profile["name"], explicit=body.get("agentId"))

    parent = body.get("parentAgentId") or None
    if parent is not None:
        _require(bool(AGENT_ID_RE.match(parent)), "parentAgentId is malformed")
        _require(parent != agent_id, "an agent cannot be its own parent")

    # Built-ins are on by default in create_harness and must not be declared
    # (BUILD_PLAN gotcha 7); they are recorded so the router can reason about
    # them, not so they can be requested.
    builtin = ["shell", "file_operations"]
    extra = [t for t in (body.get("tools") or []) if t in {"browser", "code_interpreter"}]

    agent = {
        "pk": K.agent_pk(agent_id), "sk": "META",
        "entity": "Agent", "agentId": agent_id,
        "gsi1pk": "AGENTS", "gsi1sk": profile["name"],

        # ownership
        "orgId": actor.org_id,
        "createdBy": actor.user_id,
        "parentAgentId": parent,

        # profile — what Create-a-Bot shows
        "name": profile["name"],
        "role": profile["role"],
        "title": profile["title"],
        "description": profile["description"],
        "systemPrompt": profile["systemPrompt"],
        "workingStyle": profile["workingStyle"],
        "avatar": profile["avatar"],
        "accent": profile["avatar"]["color"],
        # Identity, like agentId: set once at creation and never patchable. A
        # first-Bot flag that could be edited later is a flag that could be
        # moved, and the console reads it to decide whether to offer one.
        "entrypoint": profile["entrypoint"],

        # capability
        "model": models.defaults_for(profile["modelTier"]),
        "allowedTools": builtin + extra,
        "preapproved": [],
        "toolCapabilities": {},
        "budget": budget,
        "workspace": {
            "mode": body.get("workspaceMode", "ephemeral"),
            "drivePrefix": f"agents/{agent_id}/",
            "lastSyncAt": None, "sessionBytes": 0,
        },
        "memoryNamespace": f"agents/{agent_id}/memory",

        # Which compute substrate a run for this agent needs. Additive and
        # backward-compatible: all-False (the default for an agent that predates
        # this field) routes to the default ephemeral AgentCore runtime. A
        # full-computer flag here is OR-ed with the run's own flags by
        # `compute.select_for_run`, so a persistently-desktop agent routes to
        # the EC2 Desktop escape hatch. No AWS call is made here.
        "compute": compute,

        # An agent is not runnable until the harness exists. Nothing hands
        # work to a `provisioning` row.
        "status": "provisioning",
        "state": "provisioning",
        "harnessArn": None,
        "executionRoleArn": None,
    }

    items: list[dict] = [agent]

    for grant in grants:
        items.append({
            "pk": K.agent_pk(agent_id),
            "sk": K.grant_sk(grant["connectorId"]),
            "entity": "Grant", "agentId": agent_id,
            "grantedBy": actor.user_id, "grantedAt": now_iso(),
            **grant,
        })

    # The memory namespace is a row, not a convention. Without it, "this agent
    # has no memories" and "this agent's memory was never set up" look the
    # same to every reader.
    items.append({
        "pk": K.agent_pk(agent_id), "sk": "MEMNS",
        "entity": "MemoryNamespace", "agentId": agent_id,
        "namespace": agent["memoryNamespace"], "entries": 0,
    })

    # A new Bot speaks first. The greeting is a stored row, in the same
    # transaction as the thread, so it is the same in every browser and cannot
    # exist without the agent it came from -- which a console that invented it
    # on screen could not promise. `starter` keeps it out of the model's
    # history (`agentcore.build_messages`), where a leading assistant turn
    # would be an invalid conversation.
    text, suggestions = onboarding.starter_message(
        entrypoint=profile["entrypoint"], operator=onboarding.operator_name(body))

    thread_id = f"dm-{agent_id}"
    items.append({
        "pk": K.thread_pk(thread_id), "sk": "META",
        "entity": "Thread", "threadId": thread_id,
        "gsi1pk": "THREADS", "gsi1sk": now_iso(),
        "kind": "dm", "title": profile["name"], "agentIds": [agent_id],
        "sessionId": K.session_id(thread_id),
        # The greeting is what the thread's row shows until anyone says more,
        # and its timestamp is what makes a brand-new Bot read as unread.
        **threads.touch(text, "assistant"),
    })

    greeting = {
        "pk": K.thread_pk(thread_id),
        "sk": K.message_sk(now_iso(), ordered_suffix()),
        "entity": "Message", "role": "assistant",
        "author": profile["name"], "agentId": agent_id,
        "text": text, "starter": True,
    }
    if suggestions:
        greeting["suggestions"] = suggestions
    items.append(greeting)

    items.append(audit_event(agent_id, "agent.created", actor,
                             after={"name": profile["name"],
                                    "role": profile["role"],
                                    "modelTier": profile["modelTier"],
                                    "grants": [g["connectorId"] for g in grants]},
                             detail="created via API"))

    return CreatePlan(agent_id=agent_id, items=items, agent=agent)


# --- update -----------------------------------------------------------------

#: Fields a person may change after creation. `agentId`, `orgId`, `createdBy`
#: and `memoryNamespace` are deliberately absent: they are identity, and an
#: identity that can be edited is not one.
PATCHABLE: frozenset[str] = frozenset({
    "name", "role", "title", "description", "systemPrompt", "workingStyle",
    "avatar", "budget", "allowedTools", "preapproved", "status", "modelTier",
    "parentAgentId", "toolCapabilities", "timezone", "workingHours",
})


def plan_update(existing: dict, body: dict, actor: Actor) -> tuple[dict, list[dict]]:
    """Return (changes, audit_events) for a PATCH.

    Privileged changes are audited separately from cosmetic ones, so reading
    the trail answers "who widened this agent" without wading through renames.
    """
    unknown = sorted(set(body) - PATCHABLE)
    _require(not unknown, f"not editable: {unknown}")

    assert_no_self_escalation(actor, existing["agentId"], body)

    changes: dict = {}

    profile_keys = {"name", "role", "title", "description", "systemPrompt",
                    "workingStyle", "avatar"}
    if profile_keys & set(body):
        merged = {**{k: existing.get(k) for k in profile_keys},
                  "modelTier": existing.get("model", {}).get("tier", models.DEFAULT_TIER),
                  **{k: v for k, v in body.items() if k in profile_keys}}
        validated = validate_profile(merged)
        for key in profile_keys:
            if key in body:
                changes[key] = validated[key]
        if "avatar" in body:
            changes["accent"] = validated["avatar"]["color"]
        if "name" in body:
            changes["gsi1sk"] = validated["name"]

    # When a routine may fire. Cosmetic in the sense that widening it grants
    # no capability -- it changes when work starts, never what it may do.
    schedule_keys = {"timezone", "workingHours"}
    if schedule_keys & set(body):
        changes.update(validate_schedule(body))

    if "modelTier" in body:
        tier = body["modelTier"]
        _require(tier in models.TIERS,
                 f"modelTier must be one of {sorted(models.TIERS)}")
        # Changing tier re-opens the ladder but never invents an id: modelId
        # goes back to None and must be resolved against the account again.
        changes["model"] = models.defaults_for(tier)

    if "budget" in body:
        changes["budget"] = validate_limits({"budget": body["budget"]})

    if "status" in body:
        status = body["status"]
        _require(status in {"active", "paused", "archived"},
                 "status must be active, paused or archived")
        _require(existing.get("status") != "archived" or status == "archived",
                 "an archived agent cannot be reactivated; create a new one")
        changes["status"] = status
        changes["state"] = status

    for key in ("allowedTools", "preapproved", "toolCapabilities", "parentAgentId"):
        if key in body:
            changes[key] = body[key]

    if "parentAgentId" in changes and changes["parentAgentId"] == existing["agentId"]:
        raise ValidationError("an agent cannot be its own parent")

    _require(bool(changes), "no editable fields supplied")

    events: list[dict] = []
    if "budget" in changes:
        events.append(audit_event(
            existing["agentId"], "agent.budget_changed", actor,
            before=existing.get("budget"), after=changes["budget"]))
    if {"allowedTools", "preapproved", "toolCapabilities"} & set(changes):
        events.append(audit_event(
            existing["agentId"], "agent.grants_changed", actor,
            before={k: existing.get(k) for k in
                    ("allowedTools", "preapproved", "toolCapabilities")},
            after={k: changes[k] for k in changes if k in
                   ("allowedTools", "preapproved", "toolCapabilities")}))
    if changes.get("status") in {"paused", "archived"}:
        events.append(audit_event(
            existing["agentId"], "agent.deactivated", actor,
            before={"status": existing.get("status")},
            after={"status": changes["status"]},
            detail=f"status -> {changes['status']}"))

    cosmetic = set(changes) - PRIVILEGED_FIELDS - {"accent", "gsi1sk", "model", "state"}
    if cosmetic:
        events.append(audit_event(
            existing["agentId"], "agent.updated", actor,
            before={k: existing.get(k) for k in sorted(cosmetic)},
            after={k: changes[k] for k in sorted(cosmetic)}))

    return changes, events
