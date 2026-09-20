"""Approval policy and the permission gate.

Implements `docs/architecture/10-approvals-and-evidence.md`. Two rules govern
everything here:

1. The always-approve floor cannot be removed. Settings may tighten it and may
   add to it; nothing may take a row out of it.
2. An approval authorizes one action with one set of arguments, and expires.
   Expiry means denied, never a silent grant.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum


class Capability(str, Enum):
    """Risk class of a tool. Drives the approval policy, so this is a security
    decision made when a connector's catalog is written, not documentation."""
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    ADMIN = "admin"
    COST = "cost"


#: Classes that always need a decision and can never be pre-approved.
NEVER_PREAPPROVABLE: frozenset[Capability] = frozenset({
    Capability.DESTRUCTIVE,
    Capability.ADMIN,
    Capability.COST,
})

#: Tools that always require an approval regardless of capability class,
#: grant, or settings. The non-removable floor. Patterns ending in `.*` match
#: by prefix. See docs/architecture/10 for the prose version.
ALWAYS_APPROVE: frozenset[str] = frozenset({
    # Code and repositories
    "pr.merge", "repo.push_default_branch", "repo.force_push",
    "repo.delete", "branch.delete", "tag.delete",
    "repo.settings.*", "repo.secrets.*", "repo.webhooks.*",
    "ci.workflow.*", "package.publish", "release.create",
    # AWS and infrastructure
    "aws.iam.*", "aws.change.*", "aws.delete.*", "aws.deploy.production",
    "aws.security_group.*", "aws.logging.disable", "aws.cloudtrail.*",
    # Communication and identity
    "email.send", "slack.post", "calendar.write_with_attendees",
    "connector.authorize", "connector.revoke",
    # Data and money
    "file.delete_outside_workspace", "file.transfer_offplatform",
    "payment.*", "bulk.*", "data.export_sensitive",
    # Platform and agents
    "agent.create", "agent.grant", "agent.delete", "workspace.reset", "evidence.delete",
    "skill.create",
    "budget.raise", "approval.disable",
    "device.register", "device.modify", "device.unpause", "device.action.*",
})

#: Capabilities no grant may ever confer. Requests are refused outright; there
#: is no approval that unlocks these.
NEVER_APPROVABLE: frozenset[str] = frozenset({
    "org.admin.*",
    "aws.admin_credential",
    "audit.disable",
})

#: How long a pending approval stays live before it expires to denied.
EXPIRY: dict[Capability, timedelta] = {
    Capability.READ: timedelta(hours=24),
    Capability.WRITE: timedelta(hours=24),
    Capability.DESTRUCTIVE: timedelta(minutes=15),
    Capability.ADMIN: timedelta(minutes=15),
    Capability.COST: timedelta(minutes=15),
}


def matching_pattern(tool: str, patterns: frozenset[str]) -> str | None:
    """The pattern in `patterns` that covers `tool`, or None.

    Sorted so the answer is stable when two patterns overlap: a decision that
    names a different rule on a retry would be a decision nobody could audit.
    """
    for p in sorted(patterns):
        if p.endswith(".*"):
            if tool == p[:-2] or tool.startswith(p[:-1]):
                return p
        elif tool == p:
            return p
    return None


def _matches(tool: str, patterns: frozenset[str]) -> bool:
    return matching_pattern(tool, patterns) is not None


class Refused(PermissionError):
    """Raised for a capability that no approval can unlock.

    `matched` is the never-approvable pattern that refused it, so the console can
    name the rule rather than only say "no".
    """

    matched: str = ""


@dataclass(frozen=True)
class Decision:
    required: bool
    reason: str
    expires_in: timedelta | None = None
    #: Which rule decided, as a stable key the console explains in its own
    #: words: `floor`, `capability`, `read`, `preapproved` or `default`. The
    #: `reason` string is for a log; this is for a switch statement.
    rule: str = ""
    #: What that rule matched: the floor pattern that fired, or the capability
    #: class. Empty when there is nothing more specific to say.
    matched: str = ""


def evaluate(
    tool: str,
    capability: Capability,
    *,
    preapproved: frozenset[str] | set[str] = frozenset(),
) -> Decision:
    """Decide whether `tool` needs an approval before it may run.

    `preapproved` is the agent's narrow pre-approved rule set from its Access
    tab. It is consulted last and can never override the floor.
    """
    never = matching_pattern(tool, NEVER_APPROVABLE)
    if never:
        refused = Refused(f"{tool} is never approvable")
        refused.matched = never
        raise refused

    floor = matching_pattern(tool, ALWAYS_APPROVE)
    if floor:
        return Decision(True, "on the always-approve floor", EXPIRY[capability],
                        rule="floor", matched=floor)

    if capability in NEVER_PREAPPROVABLE:
        return Decision(True, f"{capability.value} capability", EXPIRY[capability],
                        rule="capability", matched=capability.value)

    if capability is Capability.READ:
        return Decision(False, "read-only", rule="read", matched=capability.value)

    if tool in preapproved:
        return Decision(False, "covered by a pre-approved rule",
                        rule="preapproved", matched=tool)

    return Decision(True, "write capability without a pre-approved rule",
                    EXPIRY[capability], rule="default", matched=capability.value)


def expires_at(capability: Capability, *, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now + EXPIRY[capability]


def bind_arguments(args: dict) -> str:
    """Hash the exact arguments an approval authorizes.

    Closes the substitution gap: an approval for `desiredCount 2 -> 4` cannot
    be spent on `2 -> 40`. Keys are sorted so an equivalent dict always hashes
    the same.
    """
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def binding_holds(approval_binding: str, args: dict) -> bool:
    return bind_arguments(args) == approval_binding


def is_expired(expires_at_iso: str, *, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    deadline = datetime.fromisoformat(expires_at_iso)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return now >= deadline
