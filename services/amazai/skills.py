"""Skills: a shared, versioned playbook library -- not per-agent brain wiring.

Implements `docs/architecture/17-message-and-memory-authorization.md` §3.
Three invariants this module exists to hold:

1. **Immutable versions.** A skill's contract -- its body, allowed tools,
   allowed capabilities, approval requirement, test status -- never changes
   in place. A change is a new `V#<n>` row; the old one is still there, so
   an agent already assigned an older version keeps exactly what it was
   assigned, and any audit of "what did agent X actually run" points at a
   row that cannot have moved since.
2. **Assignment, not injection.** A skill being `active` is necessary but no
   longer sufficient for it to reach an agent's prompt: it must also be
   *assigned* to that agent, at a specific version, via a row under the
   agent's own partition (see `assign`/`assigned_active_skills`).
3. **Declared tools are a ceiling, never a grant.** `bounded_tools` can only
   narrow a resolution the router already produced from real grants; a
   skill claiming a tool nobody granted the agent contributes nothing.

Two authoring paths, mirroring `agents.py`:

- **A person authors a skill directly.** `POST /skills` goes straight to
  `status: active`, version 1, no approval needed -- the same reasoning as a
  person creating an agent directly.
- **An agent may only propose one.** `propose_skill` never writes a skill
  row; it produces an approval card. A version bump that changes tools,
  capabilities or approval requirement needs the same human approval,
  whoever proposed it -- see `version_needs_approval`.
"""

from __future__ import annotations

import re

from amazai import keys as K
from amazai.store import Store, now_iso

NAME_RE = re.compile(r"^[\w][\w \-'&,.()/]{1,79}$")

STATUSES: frozenset[str] = frozenset({"proposed", "active", "disabled"})
TEST_STATUSES: frozenset[str] = frozenset({"untested", "passing", "failing"})

MAX_DESCRIPTION = 500
MAX_BODY = 4000

#: Fields that define a skill's *contract*, not just its wording. A version
#: bump that touches any of these needs a human's approval; a body/
#: description-only edit does not.
CONTRACT_FIELDS: frozenset[str] = frozenset({
    "allowedTools", "allowedCapabilities", "approvalRequired",
})


class ValidationError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def validate_skill(body: dict) -> dict:
    """Check the human- or agent-proposed half of a skill version."""
    name = (body.get("name") or "").strip()
    _require(bool(NAME_RE.match(name)),
             "name must be 2-80 characters of letters, digits or - ' & , . ( ) /")

    description = (body.get("description") or "").strip()
    _require(1 <= len(description) <= MAX_DESCRIPTION,
             f"description must be 1-{MAX_DESCRIPTION} characters")

    skill_body = (body.get("body") or "").strip()
    _require(len(skill_body) <= MAX_BODY,
             f"body must be at most {MAX_BODY} characters")

    test_status = body.get("testStatus") or "untested"
    _require(test_status in TEST_STATUSES,
             f"testStatus must be one of {sorted(TEST_STATUSES)}")

    return {
        "name": name, "description": description, "body": skill_body,
        "owner": body.get("owner") or body.get("createdBy") or "",
        "inputContract": body.get("inputContract") or "",
        "outputContract": body.get("outputContract") or "",
        "allowedTools": list(body.get("allowedTools") or []),
        "allowedCapabilities": list(body.get("allowedCapabilities") or []),
        "approvalRequired": bool(body.get("approvalRequired", False)),
        "testStatus": test_status,
    }


def normalize_skill_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]
    _require(len(slug) >= 2, f"cannot derive a skill id from {name!r}")
    return slug


def _version_item(fields: dict, *, skill_id: str, version: int, created_by: str,
                  approved_by: str | None) -> dict:
    return {
        "pk": K.skill_pk(skill_id), "sk": K.skill_version_sk(version),
        "entity": "SkillVersion", "skillId": skill_id, "version": version,
        "body": fields["body"],
        "inputContract": fields["inputContract"], "outputContract": fields["outputContract"],
        "allowedTools": fields["allowedTools"], "allowedCapabilities": fields["allowedCapabilities"],
        "approvalRequired": fields["approvalRequired"], "testStatus": fields["testStatus"],
        "createdBy": created_by, "createdAt": now_iso(), "approvedBy": approved_by,
    }


def plan_create(body: dict, *, created_by: str, proposed_by: str | None = None) -> tuple[dict, dict]:
    """Build the skill's META row and its immutable v1 row.

    `proposed_by` set means an agent nominated it and it is not yet usable
    by anyone -- `status: proposed` -- until `activate()` runs. A person
    authoring directly gets `status: active` immediately, version 1, no
    approval step; that is `plan_create`'s only privileged shortcut, exactly
    mirroring `agents.plan_create`'s "a person creates directly" rule.
    """
    fields = validate_skill(body)
    skill_id = normalize_skill_id(fields["name"])
    status = "proposed" if proposed_by else "active"
    approved_by = None if proposed_by else created_by

    meta = {
        "pk": K.skill_pk(skill_id), "sk": "META",
        "entity": "Skill", "skillId": skill_id,
        "gsi1pk": "SKILLS", "gsi1sk": fields["name"],
        "scope": "org",
        "name": fields["name"], "description": fields["description"],
        "owner": fields["owner"] or created_by,
        "status": status,
        "currentVersion": 1,
        "proposedBy": proposed_by,
        "createdBy": created_by,
        "usedCount": 0,
    }
    version = _version_item(fields, skill_id=skill_id, version=1,
                            created_by=created_by, approved_by=approved_by)
    return meta, version


def create(store: Store, body: dict, *, created_by: str, proposed_by: str | None = None) -> dict:
    """Write a skill's META + v1 rows atomically. See `plan_create`."""
    meta, version = plan_create(body, created_by=created_by, proposed_by=proposed_by)
    written = store.transact_put([meta, version])
    return written[0]


def activate(existing: dict) -> dict:
    """A person approves a proposed skill. `proposedBy` is kept for
    provenance -- who nominated it never changes, only who may use it."""
    return {"status": "active", "activatedAt": now_iso()}


def latest_version(store: Store, skill_id: str) -> dict | None:
    rows = store.query(K.skill_pk(skill_id), sk_prefix="V#", limit=1000)
    return rows[-1] if rows else None


def version_needs_approval(old_version: dict, new_fields: dict) -> bool:
    """Whether a version bump must go through a human before anyone can use it.

    Only a change to the fields in `CONTRACT_FIELDS` counts -- a body or
    description rewrite that keeps the same tools, capabilities and approval
    posture is not a privilege change and does not need to pause on one.
    """
    for field in CONTRACT_FIELDS:
        if old_version.get(field) != new_fields.get(field):
            return True
    return False


def propose_version(fields: dict, *, skill_id: str, next_version: int, created_by: str) -> dict:
    """A pending version row, unusable (not `currentVersion` yet) until a
    person approves it via `apply_version`."""
    row = _version_item(fields, skill_id=skill_id, version=next_version,
                        created_by=created_by, approved_by=None)
    row["entity"] = "SkillVersionProposal"
    return row


def apply_version(store: Store, skill_id: str, version_row: dict, *, approved_by: str) -> dict:
    """A person approves a version bump: the row becomes the immutable
    current version and the skill META pointer moves onto it."""
    version_row = dict(version_row)
    version_row["entity"] = "SkillVersion"
    version_row["approvedBy"] = approved_by
    store.put(version_row)
    store.update(K.skill_pk(skill_id), "META", {"currentVersion": version_row["version"]})
    return version_row


def active_skills(rows: list[dict]) -> list[dict]:
    """The subset that assignment is even allowed to draw from."""
    return [r for r in rows if r.get("status") == "active"]


def assign(store: Store, *, skill_id: str, agent_id: str, version: int, assigned_by: str) -> dict:
    """Make one agent eligible to see one skill, pinned to one version.

    Stored under the agent's own partition -- the same shape as `MEM#` --
    so `assigned_active_skills` is a single query, not a scan of every skill
    followed by a membership check.
    """
    return store.put({
        "pk": K.agent_pk(agent_id), "sk": f"SKILLASSIGN#{skill_id}",
        "entity": "SkillAssignment", "skillId": skill_id, "agentId": agent_id,
        "version": version, "assignedBy": assigned_by, "assignedAt": now_iso(),
    })


def unassign(store: Store, *, skill_id: str, agent_id: str) -> None:
    store.delete(K.agent_pk(agent_id), f"SKILLASSIGN#{skill_id}")


def assigned_active_skills(store: Store, agent_id: str) -> list[dict]:
    """Exactly what `build_system_prompt` is allowed to inject for one agent:
    active skills, assigned to this agent, at the version it was assigned --
    never a newer version it was never granted."""
    assignments = store.query(K.agent_pk(agent_id), sk_prefix="SKILLASSIGN#", limit=200)
    out: list[dict] = []
    for a in assignments:
        skill = store.try_get(K.skill_pk(a["skillId"]), "META")
        if not skill or skill.get("status") != "active":
            continue
        version_row = store.try_get(K.skill_pk(a["skillId"]), K.skill_version_sk(a["version"]))
        if not version_row:
            continue
        out.append({**skill, **version_row, "assignedVersion": a["version"]})
    return out


def bounded_tools(skill_version: dict, resolved_tools: frozenset[str]) -> frozenset[str]:
    """A skill's declared `allowedTools` can only narrow what the agent's own
    grant-driven resolution already produced -- it is a usage contract, not
    a second source of tool grants. An unrecognized tool in the declaration
    that resolution never produced contributes nothing here."""
    declared = frozenset(skill_version.get("allowedTools") or [])
    if not declared:
        return frozenset(resolved_tools)
    return frozenset(resolved_tools) & declared
