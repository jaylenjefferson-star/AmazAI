"""The human Directory and RBAC engine.

Every test asserts a boundary that would actually regress: the matrix cell
values, the pull-based fail-closed rule for non-ACTIVE members, and the
last-Owner floor. The shape of a member row may change; who holds what must
not.
"""

import pytest

from amazai import directory as D, keys as K
from amazai.agents import Escalation, ValidationError

ORG = "org-1"


def active(subject, role):
    return D.Membership(subject=subject, role=role)


# --- the capability matrix --------------------------------------------------

#: The blueprint §4 roles matrix, transcribed independently of the module so
#: the test fails if CAPABILITIES is edited to disagree with it. Each cell is
#: (capability, role) -> allowed.
EXPECTED = {
    D.Capability.INVITE_USERS: {D.Role.OWNER, D.Role.ADMIN},
    D.Capability.CHANGE_ORG_POLICIES: {D.Role.OWNER, D.Role.ADMIN, D.Role.SECURITY},
    D.Capability.ENFORCE_AUTO_REVIEW: {D.Role.OWNER, D.Role.SECURITY},
    D.Capability.TERMINATE_COMPUTER: {D.Role.OWNER, D.Role.ADMIN, D.Role.SECURITY},
    D.Capability.SEE_ALL_USAGE: {D.Role.OWNER, D.Role.BILLING, D.Role.AUDITOR},
    D.Capability.MANAGE_PAYMENT: {D.Role.OWNER, D.Role.BILLING},
    D.Capability.IMPERSONATE: {D.Role.OWNER, D.Role.ADMIN},
    D.Capability.READ_AUDIT_LOG: {D.Role.OWNER, D.Role.ADMIN, D.Role.SECURITY, D.Role.AUDITOR},
    D.Capability.CREATE_BOT_TEMPLATES: {D.Role.OWNER, D.Role.ADMIN},
    D.Capability.USE_MEMBER_APP: {
        D.Role.OWNER, D.Role.ADMIN, D.Role.SECURITY, D.Role.BILLING,
        D.Role.MEMBER, D.Role.AUDITOR,
    },
}


class TestTheMatrix:
    def test_the_six_roles_are_exactly_the_blueprint_roles(self):
        assert {r.value for r in D.Role} == {
            "owner", "admin", "security", "billing", "member", "auditor",
        }

    @pytest.mark.parametrize("capability", list(D.Capability))
    @pytest.mark.parametrize("role", list(D.Role))
    def test_every_cell_matches_the_blueprint(self, capability, role):
        """Parametrized over every (role, capability) pair, asserting both the
        allowed and the denied cells -- a denied cell that silently becomes
        allowed is exactly the regression this guards."""
        expected = role in EXPECTED[capability]
        assert D.can(active("u", role), capability) is expected

    def test_auditor_is_read_only(self):
        """The read-only Auditor may read the audit log and use the member app
        but holds no action capability."""
        auditor = active("u", D.Role.AUDITOR)
        assert D.can(auditor, D.Capability.READ_AUDIT_LOG)
        assert D.can(auditor, D.Capability.USE_MEMBER_APP)
        assert not D.can(auditor, D.Capability.CHANGE_ORG_POLICIES)
        assert not D.can(auditor, D.Capability.TERMINATE_COMPUTER)
        assert not D.can(auditor, D.Capability.MANAGE_PAYMENT)

    def test_a_member_does_not_see_all_usage(self):
        """A Member sees their own usage only, which is the absence of
        see_all_usage rather than a variant of it."""
        assert not D.can(active("u", D.Role.MEMBER), D.Capability.SEE_ALL_USAGE)


# --- pull-based fail-closed -------------------------------------------------

class TestNonActiveHoldsNothing:
    @pytest.mark.parametrize("state", [D.MemberState.INVITED, D.MemberState.SUSPENDED])
    @pytest.mark.parametrize("role", list(D.Role))
    @pytest.mark.parametrize("capability", list(D.Capability))
    def test_a_non_active_member_holds_zero_capabilities(self, state, role, capability):
        """Regardless of role -- an invited or suspended Owner holds nothing
        until the seat is ACTIVE. This is the pull-based cut: a suspension
        takes effect on the very next check."""
        m = D.Membership(subject="u", role=role, state=state)
        assert D.can(m, capability) is False

    def test_assert_can_agrees_with_can(self):
        """can() and assert_can() must never disagree, or an approval path and
        a denial path would diverge."""
        for role in D.Role:
            for cap in D.Capability:
                m = active("u", role)
                if D.can(m, cap):
                    D.assert_can(m, cap)  # does not raise
                else:
                    with pytest.raises(Escalation):
                        D.assert_can(m, cap)

    def test_assert_can_raises_for_a_suspended_owner(self):
        suspended = D.Membership(subject="u", role=D.Role.OWNER,
                                 state=D.MemberState.SUSPENDED)
        with pytest.raises(Escalation):
            D.assert_can(suspended, D.Capability.CHANGE_ORG_POLICIES)


# --- the last-Owner floor ---------------------------------------------------

class TestTheLastOwner:
    def test_the_last_owner_cannot_be_suspended(self):
        owner = active("owner", D.Role.OWNER)
        with pytest.raises(ValidationError):
            D.suspend_patch(owner, [owner, active("m", D.Role.MEMBER)])

    def test_the_last_owner_cannot_be_demoted(self):
        owner = active("owner", D.Role.OWNER)
        with pytest.raises(ValidationError):
            D.change_role_patch(owner, D.Role.ADMIN, [owner])

    def test_an_owner_can_be_suspended_when_another_owner_remains(self):
        one, two = active("o1", D.Role.OWNER), active("o2", D.Role.OWNER)
        patch = D.suspend_patch(one, [one, two])
        assert patch["state"] == D.MemberState.SUSPENDED.value

    def test_an_owner_can_be_demoted_when_another_owner_remains(self):
        one, two = active("o1", D.Role.OWNER), active("o2", D.Role.OWNER)
        patch = D.change_role_patch(one, D.Role.ADMIN, [one, two])
        assert patch["role"] == D.Role.ADMIN.value

    def test_a_suspended_owner_does_not_count_toward_the_floor(self):
        """A suspended Owner cannot govern, so it must not keep the last active
        Owner from being protected -- suspending the sole ACTIVE owner is still
        refused even when a suspended owner row exists."""
        active_owner = active("o1", D.Role.OWNER)
        dormant = D.Membership(subject="o2", role=D.Role.OWNER,
                               state=D.MemberState.SUSPENDED)
        with pytest.raises(ValidationError):
            D.suspend_patch(active_owner, [active_owner, dormant])

    def test_a_non_owner_is_never_the_last_owner(self):
        member = active("m", D.Role.MEMBER)
        assert D.suspend_patch(member, [active("o", D.Role.OWNER), member])


# --- row builders and the scope tuple ---------------------------------------

class TestRowBuilders:
    def test_invite_produces_an_invited_seat(self):
        row = D.invite_member(ORG, "auth0|new", D.Role.MEMBER, invited_by="auth0|owner")
        assert row["pk"] == K.org_pk(ORG)
        assert row["sk"] == K.member_sk("auth0|new")
        assert row["state"] == D.MemberState.INVITED.value
        assert row["invitedBy"] == "auth0|owner"
        # An invited member holds nothing until ACTIVE.
        assert not D.can(D.membership_of(row), D.Capability.USE_MEMBER_APP)

    def test_the_scope_tuple_round_trips_through_a_row(self):
        row = D.member_row(ORG, "auth0|s", D.Role.SECURITY,
                           scope=D.Scope.WORKSPACE, scope_id="ws-7")
        back = D.membership_of(row)
        assert back.subject == "auth0|s"
        assert back.role is D.Role.SECURITY
        assert back.scope is D.Scope.WORKSPACE
        assert back.scope_id == "ws-7"
        assert back.state is D.MemberState.ACTIVE

    def test_org_scope_defaults_scope_id_to_the_org(self):
        row = D.member_row(ORG, "auth0|a", D.Role.ADMIN)
        assert row["scope"] == D.Scope.ORG.value
        assert row["scopeId"] == ORG

    def test_a_member_row_needs_a_subject(self):
        with pytest.raises(ValidationError):
            D.member_row(ORG, "", D.Role.MEMBER)

    def test_reactivate_returns_a_member_to_active(self):
        suspended = D.Membership(subject="u", role=D.Role.MEMBER,
                                 state=D.MemberState.SUSPENDED)
        assert D.reactivate_patch(suspended)["state"] == D.MemberState.ACTIVE.value

    def test_the_scope_hierarchy_is_platform_org_workspace_bot(self):
        assert [s.value for s in D.Scope] == ["platform", "org", "workspace", "bot"]


class TestPersistedRows:
    def test_a_member_row_is_owner_isolated_on_the_shared_table(self, two_stores):
        """The row still carries ownerId like every other row, so the store's
        tenant isolation covers the governance layer too."""
        a, b = two_stores
        a.put(D.member_row("org-a", "auth0|x", D.Role.ADMIN))
        assert a.try_get(K.org_pk("org-a"), K.member_sk("auth0|x")) is not None
        assert b.try_get(K.org_pk("org-a"), K.member_sk("auth0|x")) is None
