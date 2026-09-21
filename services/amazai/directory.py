"""The human Directory and the RBAC engine.

SSO and authentication already happen at Auth0; this is the layer that decides
what a *verified* human may do once they are in. It follows the same rule as
every other authz decision in this repository: a capability a principal lacks
is ABSENT, not present-and-refused. `can()` returns False and `assert_can()`
raises; there is no code path that consults a prompt to make an action safe.

Three properties shape the module, and each is a security decision rather than
a preference:

1. **Enforcement is pull-based.** A membership's capabilities are computed
   from its *current* state on every check. A suspended human's next gated
   action fails closed, including an in-flight routine the human started
   before being suspended -- see `can()`'s ACTIVE gate and `govern.guard_principal`.

2. **The matrix is code, not documentation.** `CAPABILITIES` is transcribed
   from the blueprint roles matrix and is the single source of truth for who
   holds what. A role that is not listed for a capability does not hold it.

3. **The last Owner is load-bearing.** Suspending or demoting the final Owner
   would leave an org no one can govern, so both raise ValidationError.

Rows are RETURNED as dicts for the caller to persist, the same pattern as
agents.audit_event and agents.plan_create. Nothing here touches the store, so
every rule above is testable without a table.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from amazai import keys as K
from amazai.agents import Escalation, ValidationError
from amazai.store import now_iso


class Role(str, Enum):
    """The six blueprint roles. AUDITOR is read-only."""
    OWNER = "owner"
    ADMIN = "admin"
    SECURITY = "security"
    BILLING = "billing"
    MEMBER = "member"
    AUDITOR = "auditor"


class Scope(str, Enum):
    """The scope hierarchy PLATFORM > ORG > WORKSPACE > BOT.

    Only ORG is ever written in v1. The tuple (subject, role, scope, scope_id)
    is modelled now so that workspace-scoped roles later are a feature rather
    than a migration -- see docs/architecture/03-data-model.md.
    """
    PLATFORM = "platform"
    ORG = "org"
    WORKSPACE = "workspace"
    BOT = "bot"


class MemberState(str, Enum):
    """Invite lifecycle. Only ACTIVE holds capabilities; offboarding is a
    move to SUSPENDED, never a delete, so the audit trail survives it."""
    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"


# --- the capability matrix --------------------------------------------------

#: Every admin capability the matrix names. `use_member_app` is the ordinary
#: member chat surface; the rest are governance actions.
class Capability(str, Enum):
    INVITE_USERS = "invite_users"
    CHANGE_ORG_POLICIES = "change_org_policies"
    ENFORCE_AUTO_REVIEW = "enforce_auto_review"
    TERMINATE_COMPUTER = "terminate_computer"
    SEE_ALL_USAGE = "see_all_usage"
    MANAGE_PAYMENT = "manage_payment"
    IMPERSONATE = "impersonate"
    READ_AUDIT_LOG = "read_audit_log"
    CREATE_BOT_TEMPLATES = "create_bot_templates"
    USE_MEMBER_APP = "use_member_app"


#: Transcribed EXACTLY from the blueprint roles matrix. A role holds a
#: capability iff it appears in that capability's set. This is the whole of
#: RBAC; there is no inheritance and no wildcard -- an unlisted cell is a
#: denied cell, which is what keeps "Auditor is read-only" true by
#: construction rather than by a check someone might forget.
CAPABILITIES: dict[Capability, frozenset[Role]] = {
    Capability.INVITE_USERS: frozenset({Role.OWNER, Role.ADMIN}),
    Capability.CHANGE_ORG_POLICIES: frozenset({Role.OWNER, Role.ADMIN, Role.SECURITY}),
    Capability.ENFORCE_AUTO_REVIEW: frozenset({Role.OWNER, Role.SECURITY}),
    Capability.TERMINATE_COMPUTER: frozenset({Role.OWNER, Role.ADMIN, Role.SECURITY}),
    # Billing and the read-only Auditor see the whole bill; a Member sees only
    # their own usage, which is the absence of this capability, not a variant
    # of it.
    Capability.SEE_ALL_USAGE: frozenset({Role.OWNER, Role.BILLING, Role.AUDITOR}),
    Capability.MANAGE_PAYMENT: frozenset({Role.OWNER, Role.BILLING}),
    # Admin's impersonate is the break-glass path; it is still a capability
    # gated here, and every use is audited (see govern.ADMIN_AUDITED_ACTIONS).
    Capability.IMPERSONATE: frozenset({Role.OWNER, Role.ADMIN}),
    Capability.READ_AUDIT_LOG: frozenset({Role.OWNER, Role.ADMIN, Role.SECURITY, Role.AUDITOR}),
    Capability.CREATE_BOT_TEMPLATES: frozenset({Role.OWNER, Role.ADMIN}),
    # Everyone with a seat can use the member chat app, the Auditor included.
    Capability.USE_MEMBER_APP: frozenset({
        Role.OWNER, Role.ADMIN, Role.SECURITY, Role.BILLING, Role.MEMBER, Role.AUDITOR,
    }),
}


@dataclass(frozen=True)
class Membership:
    """A human's standing in an org: who they are, what role, at what scope,
    and whether the seat is live.

    `scope_id` is the identifier of the thing the role applies to -- the orgId
    for an ORG-scoped role. Frozen because a membership is a fact at a point in
    time; a state change is a new row (or a patch), not a mutation of this
    object.
    """
    subject: str
    role: Role
    scope: Scope = Scope.ORG
    scope_id: str = ""
    state: MemberState = MemberState.ACTIVE

    @property
    def is_active(self) -> bool:
        return self.state is MemberState.ACTIVE


def can(membership: Membership, capability: Capability) -> bool:
    """Whether `membership` holds `capability` right now.

    Pull-based fail-closed: a non-ACTIVE member holds ZERO capabilities
    regardless of role. An invited human has not accepted yet; a suspended one
    has been offboarded. Either way the answer is False for everything, so a
    suspension takes effect on the very next action rather than at some later
    sync.
    """
    if not membership.is_active:
        return False
    return membership.role in CAPABILITIES[capability]


def assert_can(membership: Membership, capability: Capability) -> None:
    """`can()`, but raising an Escalation the api layer maps to 403.

    Uses the same exception agents.py raises for an over-reach so the HTTP
    mapping in handlers/api.py already covers it.
    """
    if not can(membership, capability):
        raise Escalation(
            f"{membership.role.value!r} at {membership.state.value!r} may not "
            f"{capability.value}"
        )


# --- row builders -----------------------------------------------------------
#
# These RETURN dicts; the caller persists them (usually via store.transact_put
# for an invite that also writes an audit row). None of them touch the store.

def _member_row(org_id: str, subject: str, role: Role, state: MemberState, *,
                scope: Scope = Scope.ORG, scope_id: str | None = None,
                invited_by: str | None = None) -> dict:
    return {
        "pk": K.org_pk(org_id),
        "sk": K.member_sk(subject),
        "entity": "Member",
        "gsi1pk": "MEMBERS", "gsi1sk": f"{org_id}#{subject}",
        "orgId": org_id,
        "subject": subject,
        "role": role.value,
        "scope": scope.value,
        # The orgId is the scope_id for an ORG-scoped role; carried explicitly
        # so a WORKSPACE- or BOT-scoped row later reads the same way.
        "scopeId": scope_id if scope_id is not None else org_id,
        "state": state.value,
        "invitedBy": invited_by,
        "invitedAt": now_iso(),
    }


def invite_member(org_id: str, subject: str, role: Role, *,
                  invited_by: str, scope: Scope = Scope.ORG,
                  scope_id: str | None = None) -> dict:
    """A newly invited member: role assigned, seat not yet live (INVITED).

    An invited human holds no capabilities until they accept and the row moves
    to ACTIVE -- `can()` enforces that, so the INVITED state is not a trust
    grant, only a reservation.
    """
    if not subject:
        raise ValidationError("a member needs a subject")
    return _member_row(org_id, subject, role, MemberState.INVITED,
                       scope=scope, scope_id=scope_id, invited_by=invited_by)


def member_row(org_id: str, subject: str, role: Role, *,
               state: MemberState = MemberState.ACTIVE,
               scope: Scope = Scope.ORG, scope_id: str | None = None) -> dict:
    """An arbitrary membership row, e.g. the implicit Owner or a seeded team."""
    if not subject:
        raise ValidationError("a member needs a subject")
    return _member_row(org_id, subject, role, state, scope=scope, scope_id=scope_id)


def membership_of(row: dict) -> Membership:
    """Reconstruct a Membership from a stored row.

    The inverse of the builders, so the scope tuple round-trips: what was
    written is what is enforced.
    """
    return Membership(
        subject=row["subject"],
        role=Role(row["role"]),
        scope=Scope(row.get("scope", Scope.ORG.value)),
        scope_id=row.get("scopeId", ""),
        state=MemberState(row["state"]),
    )


def _owners(members: list[Membership]) -> list[Membership]:
    """ACTIVE Owners. A suspended Owner does not count toward the floor: the
    guard protects the org's ability to be governed, and a suspended Owner
    cannot govern it."""
    return [m for m in members if m.role is Role.OWNER and m.is_active]


def suspend_patch(target: Membership, members: list[Membership]) -> dict:
    """The store patch that suspends `target`, refusing to cut the last Owner.

    `members` is the current org roster, passed in rather than looked up so
    the last-Owner rule stays testable without a table. Returns a dict for
    store.update -- an offboard is a state change, never a delete, so the
    member's audit trail survives it.
    """
    if target.role is Role.OWNER and len(_owners(members)) <= 1:
        raise ValidationError(
            "the last Owner cannot be suspended; assign another Owner first"
        )
    return {"state": MemberState.SUSPENDED.value}


def reactivate_patch(target: Membership) -> dict:
    """The store patch that returns an invited or suspended member to ACTIVE."""
    return {"state": MemberState.ACTIVE.value}


def change_role_patch(target: Membership, new_role: Role,
                      members: list[Membership]) -> dict:
    """The store patch that changes `target`'s role, refusing to demote the
    last Owner. Demoting the final Owner is the same org-ending move as
    suspending them, so it is refused the same way."""
    if (target.role is Role.OWNER and new_role is not Role.OWNER
            and len(_owners(members)) <= 1):
        raise ValidationError(
            "the last Owner cannot be demoted; assign another Owner first"
        )
    return {"role": new_role.value}
