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

#: Connector toolkits whose write actions can reach a real person outside
#: AmazAI directly -- email, chat, SMS, and calendar invites. A connector-wide
#: grant (`connector_capability` in `evaluate`) never extends trust to these,
#: regardless of tier.
#:
#: This exists because the floor's own communication entries just above
#: (`email.send`, `slack.post`, `calendar.write_with_attendees`) name semantic
#: actions that nothing currently produces: `connector_call` passes a raw
#: Composio tool slug (`GMAIL_SEND_EMAIL`, `SLACK_SEND_MESSAGE`) as `tool`, and
#: Composio's own tags (`readOnlyHint`/`createHint`/`updateHint`/
#: `destructiveHint`) have no fifth tag for "reaches a person" -- so those
#: floor patterns cannot match a real connector call at all, and a send has
#: only ever been gated by the ordinary "default" write rule. That gap
#: predates this constant and is not what this fixes; what this fixes is
#: narrower and more urgent: `connector_capability` must not let a Bot's
#: connector-wide grant carry an email or a Slack post past even that
#: ordinary rule. Toolkit-scoped and conservative on purpose -- a false
#: positive here just asks once more; a false negative is the exact thing
#: the floor exists to prevent. A real fix for the underlying gap (mapping
#: specific send-shaped actions to the floor's semantic names, toolkit by
#: toolkit) is a separate, larger piece of work.
_REACHES_A_REAL_PERSON_DIRECTLY: frozenset[str] = frozenset({
    "gmail", "outlook", "office365", "slack", "discord", "microsoft_teams",
    "twilio", "whatsapp", "telegram", "sms", "googlecalendar", "outlook_calendar",
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
    connector_capability: Capability | None = None,
    connector_toolkit: str = "",
) -> Decision:
    """Decide whether `tool` needs an approval before it may run.

    `preapproved` is the agent's narrow pre-approved rule set from its Access
    tab. It is consulted last and can never override the floor.

    `connector_capability` is the Bot's own granted ceiling for the connector
    this call belongs to (`None` for anything that is not a connector call --
    `agent.create`, `skill.create`, and the like pass nothing here and are
    unaffected). Granting a connector above read-only is already the
    operator's own explicit decision to let this Bot write through that app
    (`docs/connectors.md`): connecting it, then choosing Full over Read in
    its profile. An ordinary write no longer asks a second time on top of
    that. This changes nothing else: `NEVER_APPROVABLE`, the floor, and
    `NEVER_PREAPPROVABLE` capabilities (destructive/admin/cost) are all
    checked first and never reach this, so a connector-wide grant can never
    be the reason a payment, a deletion, or an email goes unreviewed.

    `connector_toolkit` narrows that trust further: a toolkit in
    `_REACHES_A_REAL_PERSON_DIRECTLY` (email, chat, SMS, calendar invites)
    is never covered by `connector_capability`, however it was granted --
    see that constant for why.
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

    if (connector_capability is not None and connector_capability is not Capability.READ
            and connector_toolkit not in _REACHES_A_REAL_PERSON_DIRECTLY):
        return Decision(False, "the connector itself was granted write access",
                        rule="connector_trusted", matched=connector_capability.value)

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
