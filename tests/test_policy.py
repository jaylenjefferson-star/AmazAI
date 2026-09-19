from datetime import datetime, timedelta, timezone

import pytest

from amazai import policy
from amazai.policy import Capability, Decision, Refused


class TestAlwaysApproveFloor:
    @pytest.mark.parametrize("tool", [
        "pr.merge", "repo.delete", "aws.iam.CreateRole", "aws.deploy.production",
        "email.send", "slack.post", "agent.grant", "budget.raise",
        "device.action.file.upload", "evidence.delete", "workspace.reset",
    ])
    def test_floor_tools_always_require_approval(self, tool):
        d = policy.evaluate(tool, Capability.WRITE)
        assert d.required, tool

    def test_preapproval_cannot_bypass_the_floor(self):
        # The Access tab may add narrow pre-approved rules. It may not remove a
        # row from the floor -- this is the check that makes that true.
        d = policy.evaluate("pr.merge", Capability.WRITE, preapproved={"pr.merge"})
        assert d.required
        assert "floor" in d.reason

    def test_wildcard_matches_by_prefix(self):
        assert policy.evaluate("aws.iam.AttachRolePolicy", Capability.WRITE).required
        assert policy.evaluate("payment.charge", Capability.WRITE).required

    def test_wildcard_does_not_match_unrelated_prefix(self):
        # "payment.*" must not swallow "paymentless.read".
        d = policy.evaluate("paymentless.read", Capability.READ)
        assert not d.required


class TestCapabilityClasses:
    def test_read_needs_no_approval(self):
        assert not policy.evaluate("issue.search", Capability.READ).required

    def test_destructive_can_never_be_preapproved(self):
        d = policy.evaluate("some.destructive.op", Capability.DESTRUCTIVE,
                            preapproved={"some.destructive.op"})
        assert d.required

    def test_admin_can_never_be_preapproved(self):
        d = policy.evaluate("some.admin.op", Capability.ADMIN,
                            preapproved={"some.admin.op"})
        assert d.required

    def test_cost_can_never_be_preapproved(self):
        d = policy.evaluate("some.cost.op", Capability.COST,
                            preapproved={"some.cost.op"})
        assert d.required

    def test_write_may_be_preapproved_when_off_the_floor(self):
        d = policy.evaluate("pr.create", Capability.WRITE, preapproved={"pr.create"})
        assert not d.required

    def test_write_without_preapproval_requires_a_decision(self):
        assert policy.evaluate("pr.create", Capability.WRITE).required


class TestNeverApprovable:
    @pytest.mark.parametrize("tool", [
        "org.admin.members", "aws.admin_credential", "audit.disable",
    ])
    def test_refused_outright(self, tool):
        # There is no approval that unlocks these; they raise rather than
        # returning "needs approval".
        with pytest.raises(Refused):
            policy.evaluate(tool, Capability.ADMIN)


class TestExpiry:
    def test_risky_classes_expire_in_fifteen_minutes(self):
        for cap in (Capability.DESTRUCTIVE, Capability.ADMIN, Capability.COST):
            assert policy.EXPIRY[cap] == timedelta(minutes=15)

    def test_write_expires_in_a_day(self):
        assert policy.EXPIRY[Capability.WRITE] == timedelta(hours=24)

    def test_expires_at_is_relative_to_now(self):
        now = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
        assert policy.expires_at(Capability.ADMIN, now=now) == now + timedelta(minutes=15)

    def test_is_expired_is_true_at_the_deadline(self):
        now = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
        assert policy.is_expired(now.isoformat(), now=now)

    def test_is_expired_false_before_the_deadline(self):
        now = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
        later = (now + timedelta(minutes=1)).isoformat()
        assert not policy.is_expired(later, now=now)

    def test_naive_timestamps_are_treated_as_utc(self):
        now = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
        assert policy.is_expired("2026-09-19T11:59:00", now=now)


class TestArgumentBinding:
    def test_identical_arguments_bind_equal(self):
        args = {"repo": "amazai", "head": "fix/x"}
        assert policy.binding_holds(policy.bind_arguments(args), dict(args))

    def test_key_order_does_not_change_the_binding(self):
        a = policy.bind_arguments({"repo": "amazai", "head": "fix/x"})
        b = policy.bind_arguments({"head": "fix/x", "repo": "amazai"})
        assert a == b

    def test_changed_value_invalidates_the_binding(self):
        # The substitution gap: an approval for desiredCount 2->4 must not be
        # spendable on 2->40.
        binding = policy.bind_arguments({"service": "api", "desiredCount": 4})
        assert not policy.binding_holds(binding, {"service": "api", "desiredCount": 40})

    def test_added_argument_invalidates_the_binding(self):
        binding = policy.bind_arguments({"repo": "amazai"})
        assert not policy.binding_holds(binding, {"repo": "amazai", "force": True})

    def test_removed_argument_invalidates_the_binding(self):
        binding = policy.bind_arguments({"repo": "amazai", "force": True})
        assert not policy.binding_holds(binding, {"repo": "amazai"})

    def test_nested_change_invalidates_the_binding(self):
        binding = policy.bind_arguments({"target": {"env": "staging"}})
        assert not policy.binding_holds(binding, {"target": {"env": "production"}})


class TestDecisionShape:
    def test_required_decisions_carry_an_expiry_window(self):
        d = policy.evaluate("pr.merge", Capability.WRITE)
        assert isinstance(d, Decision)
        assert d.expires_in is not None

    def test_read_decisions_carry_no_expiry(self):
        assert policy.evaluate("issue.search", Capability.READ).expires_in is None
