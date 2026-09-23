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


class TestWhichRuleDecided:
    """The decision names its rule, so the console can say why this one asked."""

    def test_a_floor_tool_names_the_pattern_that_caught_it(self):
        d = policy.evaluate("aws.iam.attach_policy", Capability.WRITE)
        assert (d.required, d.rule, d.matched) == (True, "floor", "aws.iam.*")

    def test_an_exact_floor_entry_names_itself(self):
        d = policy.evaluate("slack.post", Capability.WRITE)
        assert (d.rule, d.matched) == ("floor", "slack.post")

    def test_a_dangerous_capability_names_the_class(self):
        d = policy.evaluate("db.drop_table", Capability.DESTRUCTIVE)
        assert (d.rule, d.matched) == ("capability", "destructive")

    def test_a_read_says_read(self):
        assert policy.evaluate("slack.read", Capability.READ).rule == "read"

    def test_a_write_with_no_rule_says_default(self):
        d = policy.evaluate("notes.append", Capability.WRITE)
        assert (d.required, d.rule) == (True, "default")

    def test_a_preapproved_write_says_so(self):
        d = policy.evaluate("notes.append", Capability.WRITE, preapproved={"notes.append"})
        assert (d.required, d.rule, d.matched) == (False, "preapproved", "notes.append")

    def test_a_refusal_carries_the_pattern_that_refused(self):
        with pytest.raises(Refused) as exc:
            policy.evaluate("aws.admin_credential", Capability.ADMIN)
        assert exc.value.matched == "aws.admin_credential"


class TestAConnectorGrantedWriteNoLongerAsksTwice:
    """Connecting an app, then choosing Full over Read in a Bot's profile, is
    already the operator's own explicit decision to let that Bot write
    through it (docs/connectors.md). An ordinary write no longer asks a
    second time on top of that -- but only the ordinary "default" write rule
    moves; the floor, NEVER_APPROVABLE, and NEVER_PREAPPROVABLE all run
    first and are completely unaffected, so a connector-wide grant can never
    be the reason a payment, a deletion, or an email goes unreviewed."""

    def test_a_full_grant_skips_the_default_ask(self):
        d = policy.evaluate("notes.append", Capability.WRITE,
                            connector_capability=Capability.WRITE)
        assert (d.required, d.rule, d.matched) == (False, "connector_trusted", "write")

    def test_an_admin_ceiling_grant_also_counts(self):
        d = policy.evaluate("notes.append", Capability.WRITE,
                            connector_capability=Capability.ADMIN)
        assert not d.required

    def test_a_read_only_grant_still_asks(self):
        # Not reachable in practice -- connectors.authorize refuses a write
        # against a read-only grant before policy.evaluate is ever called --
        # but the function is correct in isolation regardless.
        d = policy.evaluate("notes.append", Capability.WRITE,
                            connector_capability=Capability.READ)
        assert (d.required, d.rule) == (True, "default")

    def test_no_connector_capability_behaves_exactly_as_before(self):
        d = policy.evaluate("notes.append", Capability.WRITE)
        assert (d.required, d.rule) == (True, "default")

    @pytest.mark.parametrize("tool", [
        "email.send", "slack.post", "payment.charge", "aws.iam.AttachRolePolicy",
    ])
    def test_the_floor_asks_regardless_of_connector_grant(self, tool):
        d = policy.evaluate(tool, Capability.WRITE, connector_capability=Capability.ADMIN)
        assert d.required and d.rule == "floor"

    @pytest.mark.parametrize("capability", [
        Capability.DESTRUCTIVE, Capability.ADMIN, Capability.COST,
    ])
    def test_destructive_admin_and_cost_ask_regardless_of_connector_grant(self, capability):
        d = policy.evaluate("some.risky.op", capability, connector_capability=Capability.ADMIN)
        assert d.required and d.rule == "capability"

    def test_an_explicit_pre_approval_still_wins_over_a_read_only_grant(self):
        # preapproved is checked first, so a narrower, tool-specific rule
        # still applies even when the connector-wide grant would not cover it.
        d = policy.evaluate("notes.append", Capability.WRITE,
                            preapproved={"notes.append"}, connector_capability=Capability.READ)
        assert (d.required, d.rule) == (False, "preapproved")

    @pytest.mark.parametrize("toolkit", ["gmail", "slack", "outlook", "googlecalendar", "twilio"])
    def test_email_chat_sms_and_calendar_toolkits_are_never_trusted_by_a_connector_grant(self, toolkit):
        """The floor's own email.send/slack.post/calendar.write_with_attendees
        entries can never match a real Composio slug (GMAIL_SEND_EMAIL is not
        the string "email.send"), so a send has only ever been gated by this
        "default" rule -- meaning connector_capability must not let a
        connector-wide grant carry these past it either, or this feature
        would be the thing that finally breaks what the floor was trying to
        protect and never actually could."""
        d = policy.evaluate("SOME_SEND_ACTION", Capability.WRITE,
                            connector_capability=Capability.ADMIN, connector_toolkit=toolkit)
        assert (d.required, d.rule) == (True, "default")

    def test_an_unlisted_toolkit_is_trusted_normally(self):
        d = policy.evaluate("NOTION_CREATE_PAGE", Capability.WRITE,
                            connector_capability=Capability.ADMIN, connector_toolkit="notion")
        assert (d.required, d.rule) == (False, "connector_trusted")
