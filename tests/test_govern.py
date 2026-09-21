"""The org kill switch, the principal-state gate, and the admin audit trail.

Each test asserts a fail-closed property that would be dangerous to regress:
the freeze default, the pull-based principal cut, and the audit envelope's
guarantees (correlation id always out, versioned, action-gated, chronological).
"""

import pytest

from amazai import govern as G, keys as K
from amazai.agents import Actor
from amazai.directory import Membership, MemberState, Role

ACTOR = Actor(user_id="auth0|owner", org_id="org-1")
ORG = "org-1"


# --- the kill switch --------------------------------------------------------

class TestKillSwitch:
    def test_a_missing_row_is_not_frozen(self):
        """Open by default: the absence of the row is the normal, unfrozen
        state, so a store miss must never read as frozen."""
        assert G.is_frozen(None) is False
        G.assert_not_frozen(None)  # does not raise

    def test_an_unfrozen_row_passes(self):
        row = G.killswitch_row(ORG, frozen=False, actor=ACTOR)
        assert G.is_frozen(row) is False
        G.assert_not_frozen(row)

    def test_a_frozen_row_blocks_every_action(self):
        row = G.killswitch_row(ORG, frozen=True, actor=ACTOR, reason="incident")
        assert G.is_frozen(row) is True
        with pytest.raises(G.Frozen):
            G.assert_not_frozen(row)

    def test_the_row_records_who_froze_it(self):
        row = G.killswitch_row(ORG, frozen=True, actor=ACTOR)
        assert row["pk"] == K.org_pk(ORG)
        assert row["sk"] == "KILLSWITCH"
        assert row["setBy"] == "auth0|owner"


# --- the principal-state gate -----------------------------------------------

class TestGuardPrincipal:
    def test_an_active_principal_passes(self):
        G.guard_principal(Membership(subject="u", role=Role.MEMBER))

    @pytest.mark.parametrize("state", [MemberState.INVITED, MemberState.SUSPENDED])
    def test_a_non_active_principal_is_cut(self, state):
        """The pull-based offboarding cut: a suspended human's next gated
        action fails here, even mid-routine."""
        m = Membership(subject="u", role=Role.OWNER, state=state)
        with pytest.raises(G.PrincipalNotActive):
            G.guard_principal(m)


# --- the admin audit trail --------------------------------------------------

class TestAdminAudit:
    def test_a_correlation_id_is_generated_when_absent(self):
        row = G.admin_audit_event(ORG, "member.invited", ACTOR)
        assert row["correlationId"]
        assert row["correlationId"].startswith("corr_")

    def test_a_supplied_correlation_id_is_preserved(self):
        """The join to a per-run evidence bundle: a caller threads its own id
        through so 'who approved this send' is one query."""
        row = G.admin_audit_event(ORG, "impersonation.started", ACTOR,
                                  correlation_id="corr_run_42")
        assert row["correlationId"] == "corr_run_42"

    def test_the_envelope_carries_a_version(self):
        row = G.admin_audit_event(ORG, "org.frozen", ACTOR)
        assert row["v"] == G.AUDIT_ENVELOPE_VERSION

    def test_only_enumerated_actions_are_accepted(self):
        with pytest.raises(ValueError):
            G.admin_audit_event(ORG, "member.deleted", ACTOR)

    @pytest.mark.parametrize("action", list(G.ADMIN_AUDITED_ACTIONS))
    def test_every_enumerated_action_is_accepted(self, action):
        row = G.admin_audit_event(ORG, action, ACTOR)
        assert row["action"] == action

    def test_the_row_is_shaped_for_chronological_append_only_ordering(self):
        """Under the org partition with an ordered ADMINAUDIT# sort key, so the
        org's admin history is one range query and survives what it describes."""
        row = G.admin_audit_event(ORG, "org.policy_changed", ACTOR)
        assert row["pk"] == K.org_pk(ORG)
        assert row["sk"].startswith("ADMINAUDIT#")
        assert row["entity"] == "AdminAuditEvent"

    def test_two_events_in_the_same_second_sort_distinctly(self):
        """The ordered suffix does the tie-breaking a second-resolution stamp
        cannot, so a doubled write does not overwrite or reorder."""
        a = G.admin_audit_event(ORG, "member.invited", ACTOR)
        b = G.admin_audit_event(ORG, "member.invited", ACTOR)
        assert a["sk"] != b["sk"]

    def test_the_row_persists_under_the_org_partition(self, store):
        row = G.admin_audit_event("owner-a", "member.suspended", ACTOR,
                                  before={"state": "active"},
                                  after={"state": "suspended"})
        store.put(row)
        found = store.query(K.org_pk("owner-a"), sk_prefix="ADMINAUDIT#")
        assert len(found) == 1
        assert found[0]["action"] == "member.suspended"
        assert found[0]["correlationId"]
