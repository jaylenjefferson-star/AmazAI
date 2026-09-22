"""Account-level credits.

One credit is one dollar of actual spend. The tests that matter most are the
ones a float-based balance would get subtly wrong (many small debits summing
exactly) and the ones the "no row yet" fallback exists for (an account
nothing has ever gated must never be blocked by a check that was never set
up for it).
"""

import pytest

from amazai import billing as B


class TestTheFreeTrialGrant:
    def test_first_sight_creates_the_row_with_the_trial_balance(self, store):
        row = B.ensure_billing_row(store)
        assert row["tier"] == B.TIER_TRIAL
        assert B.balance_usd(store) == B.TRIAL_GRANT_USD

    def test_a_second_call_does_not_grant_a_second_trial(self, store):
        first = B.ensure_billing_row(store)
        second = B.ensure_billing_row(store)
        assert first["createdAt"] == second["createdAt"]
        assert B.balance_usd(store) == B.TRIAL_GRANT_USD

    def test_the_grant_is_recorded_in_the_ledger(self, store):
        B.ensure_billing_row(store)
        entries = B.ledger(store)
        assert len(entries) == 1
        assert entries[0]["kind"] == "trial_grant"
        assert entries[0]["amountUsd"] == B.TRIAL_GRANT_USD


class TestNoRowYetIsOpenNotZero:
    """The same convention identity.assert_owner uses for an unconfigured
    allowlist: nothing to gate against is not the same fact as a balance of
    zero. Every real account gets a row before this can be reached in
    production (api.handler calls ensure_billing_row on every authenticated
    request); this is what keeps every test and script that drives a run
    without going through that signup path from being blocked by a check it
    never opted into."""

    def test_an_unprovisioned_account_has_credit(self, store):
        assert B.balance_usd(store) == 0.0
        assert B.has_credit(store) is True

    def test_spending_against_an_unprovisioned_account_is_a_quiet_no_op(self, store):
        result = B.spend(store, 1.0, run_id="run-1", agent_id="chief")
        assert result == 0.0
        assert B.ledger(store) == []


class TestSpending:
    def test_spend_debits_the_balance(self, store):
        B.ensure_billing_row(store)
        new_balance = B.spend(store, 1.5, run_id="run-1", agent_id="chief")
        assert new_balance == pytest.approx(B.TRIAL_GRANT_USD - 1.5)
        assert B.balance_usd(store) == pytest.approx(B.TRIAL_GRANT_USD - 1.5)

    def test_many_small_debits_sum_exactly(self, store):
        """The reason the balance is stored in micro-dollar integers, not
        float dollars: 30 debits of $0.0001 must land on exactly $4.997, not
        some float-accumulated neighbor of it."""
        B.ensure_billing_row(store)
        for _ in range(30):
            B.spend(store, 0.0001, run_id="run-x", agent_id="chief")
        assert B.balance_usd(store) == pytest.approx(B.TRIAL_GRANT_USD - 0.003, abs=1e-9)

    def test_spending_past_zero_goes_negative_not_clamped(self, store):
        # has_credit is the pre-flight gate that stops a *next* run from
        # starting; a single run already in flight is allowed to finish
        # past zero (see billing.py's module docstring).
        B.ensure_billing_row(store)
        B.spend(store, B.TRIAL_GRANT_USD + 2.0, run_id="run-1", agent_id="chief")
        assert B.balance_usd(store) == pytest.approx(-2.0)
        assert B.has_credit(store) is False

    def test_a_zero_or_negative_amount_does_not_touch_the_ledger(self, store):
        B.ensure_billing_row(store)
        B.spend(store, 0.0, run_id="run-1", agent_id="chief")
        B.spend(store, -1.0, run_id="run-1", agent_id="chief")
        assert B.balance_usd(store) == B.TRIAL_GRANT_USD
        assert len(B.ledger(store)) == 1  # only the trial grant

    def test_spend_is_recorded_with_the_run_and_agent(self, store):
        B.ensure_billing_row(store)
        B.spend(store, 0.5, run_id="run-42", agent_id="cloud-ops")
        entry = B.ledger(store)[0]
        assert entry["kind"] == "spend"
        assert entry["amountUsd"] == -0.5
        assert entry["runId"] == "run-42" and entry["agentId"] == "cloud-ops"


class TestGranting:
    def test_grant_credits_the_balance(self, store):
        B.ensure_billing_row(store)
        new_balance = B.grant(store, 20.0, kind="subscription_renewal")
        assert new_balance == pytest.approx(B.TRIAL_GRANT_USD + 20.0)

    def test_grant_provisions_the_row_if_none_exists(self, store):
        # A real payment's credit must never be dropped because the row
        # happened not to exist yet.
        new_balance = B.grant(store, 20.0, kind="purchase")
        assert new_balance == pytest.approx(B.TRIAL_GRANT_USD + 20.0)

    def test_a_replayed_stripe_event_does_not_double_the_grant(self, store):
        B.ensure_billing_row(store)
        B.grant(store, 20.0, kind="purchase", stripe_event_id="evt_123")
        again = B.grant(store, 20.0, kind="purchase", stripe_event_id="evt_123")
        assert again == pytest.approx(B.TRIAL_GRANT_USD + 20.0)

    def test_different_stripe_events_both_apply(self, store):
        B.ensure_billing_row(store)
        B.grant(store, 20.0, kind="purchase", stripe_event_id="evt_1")
        final = B.grant(store, 20.0, kind="purchase", stripe_event_id="evt_2")
        assert final == pytest.approx(B.TRIAL_GRANT_USD + 40.0)


class TestTwoOwnersDoNotShareABalance:
    def test_spending_one_owners_balance_never_touches_the_others(self, two_stores):
        store_a, store_b = two_stores
        B.ensure_billing_row(store_a)
        B.ensure_billing_row(store_b)
        B.spend(store_a, 4.0, run_id="run-a", agent_id="chief")
        assert B.balance_usd(store_a) == pytest.approx(1.0)
        assert B.balance_usd(store_b) == pytest.approx(B.TRIAL_GRANT_USD)
