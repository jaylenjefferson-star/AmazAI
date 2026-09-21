"""Org-wide governance gates and the admin audit trail.

Kept out of policy.py on purpose: policy.py owns the non-removable
always-approve *floor*, and nothing in this module may weaken it. What lives
here is the layer that runs before an action is even evaluated -- an org-wide
kill switch and a re-check of the acting human's state -- plus the append-only
admin audit envelope that records governance decisions.

Two fail-closed defaults, both deliberate:

- A missing or None kill-switch row means NOT frozen. Freezing is an explicit
  act; the absence of the row is the normal, unfrozen state.
- A non-ACTIVE principal holds nothing. `guard_principal` re-checks membership
  state on every gated action, which is the pull-based offboarding cut: an
  in-flight routine started by a human who is then suspended dies on its next
  gated action rather than running to completion.

The admin audit is DISTINCT from the per-run evidence bundle but joins to it
through one correlation id (docs/architecture/10-approvals-and-evidence.md), so
"who approved this external send" is one query. Every envelope carries a schema
version so the trail can be read years after the shape changed.

No AWS call is made from this module.
"""

from __future__ import annotations

from amazai import keys as K
from amazai.agents import Actor
from amazai.directory import Membership
from amazai.store import new_id, now_iso, ordered_suffix

#: Envelope schema version. Bumped when the audit row's shape changes so a
#: reader can tell an old row from a new one instead of guessing.
AUDIT_ENVELOPE_VERSION = 1


class Frozen(PermissionError):
    """The org is frozen by the kill switch. Surfaces as 403."""


class PrincipalNotActive(PermissionError):
    """The acting human is not ACTIVE. Surfaces as 403. This is the pull-based
    offboarding cut re-checked on each action."""


# --- the kill switch --------------------------------------------------------

def killswitch_row(org_id: str, *, frozen: bool, actor: Actor,
                   reason: str = "") -> dict:
    """The one row that freezes or unfreezes an org.

    A single row per org (SETTINGS-style, keyed under the org partition) rather
    than a per-agent flag: a freeze that any one agent could miss is not a
    freeze. `frozen` is stored explicitly so the unfrozen state is a written
    fact with a timestamp, not just the row's absence.
    """
    return {
        "pk": K.org_pk(org_id), "sk": "KILLSWITCH",
        "entity": "KillSwitch", "orgId": org_id,
        "frozen": bool(frozen),
        "reason": reason,
        "setBy": actor.user_id,
        "setAt": now_iso(),
    }


def is_frozen(killswitch_row: dict | None) -> bool:
    """Whether the org is frozen. A missing/None row means NOT frozen: the
    default state is open, and freezing is the explicit act."""
    if not killswitch_row:
        return False
    return bool(killswitch_row.get("frozen"))


def assert_not_frozen(killswitch_row: dict | None) -> None:
    """Block every gated action when the org is frozen. Fail closed on an
    explicit freeze; open when the row is absent."""
    if is_frozen(killswitch_row):
        raise Frozen("this organization is frozen by an administrator")


# --- the principal-state gate -----------------------------------------------

def guard_principal(membership: Membership) -> None:
    """Refuse a gated action when the acting human is not ACTIVE.

    Re-checked on every action rather than only at sign-in, so suspending a
    human takes effect immediately: their next gated call fails here, and any
    routine still running on their behalf dies on its next gated tool.
    """
    if not membership.is_active:
        raise PrincipalNotActive(
            f"principal {membership.subject!r} is {membership.state.value!r}, "
            "not active"
        )


# --- the admin audit trail --------------------------------------------------

#: The governance actions worth an audit row. Gated exactly like
#: agents.AUDITED_ACTIONS: an action not named here cannot be written, so a
#: typo is caught at the call site instead of producing an unqueryable row.
ADMIN_AUDITED_ACTIONS = (
    "member.invited", "member.suspended", "member.reactivated",
    "member.role_changed", "org.policy_changed", "org.frozen", "org.unfrozen",
    "harness.terminated", "connector.revoked_by_admin",
    "impersonation.started", "impersonation.ended",
)


def admin_audit_event(org_id: str, action: str, actor: Actor, *,
                      correlation_id: str | None = None,
                      before: dict | None = None, after: dict | None = None,
                      detail: str = "") -> dict:
    """An append-only record of a governance decision.

    Sorted by time under the org's own partition (ADMINAUDIT#<iso>#<suffix>)
    so the org's admin history is one chronological range query and survives
    the deletion of whatever it describes.

    The correlation id is optional-in, always-out: supply one to thread this
    row to a per-run evidence bundle, or let it be generated so every row is
    joinable regardless. `v` is the envelope version.
    """
    if action not in ADMIN_AUDITED_ACTIONS:
        raise ValueError(f"unknown admin audit action {action!r}")
    stamp = now_iso()
    return {
        "pk": K.org_pk(org_id),
        "sk": K.admin_audit_sk(stamp, ordered_suffix()),
        "entity": "AdminAuditEvent",
        "v": AUDIT_ENVELOPE_VERSION,
        "gsi1pk": "ADMINAUDIT", "gsi1sk": f"{stamp}#{org_id}",
        "orgId": org_id, "action": action, "at": stamp,
        "actorUserId": actor.user_id,
        "actorAgentId": actor.agent_id,
        "correlationId": correlation_id or new_id("corr_"),
        "before": before or {}, "after": after or {},
        "detail": detail,
    }
