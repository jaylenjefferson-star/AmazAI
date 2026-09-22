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

    def test_many_entries_in_the_same_second_still_sort_chronologically(self, store):
        # ledger() is ascending=False (most recent first); a random sort-key
        # suffix instead of store.ordered_suffix() would put same-second
        # writes in an arbitrary order (see store.ordered_suffix's own
        # docstring -- this is the exact trap it exists to avoid).
        B.ensure_billing_row(store)
        for i in range(10):
            B.spend(store, 0.01, run_id=f"run-{i}", agent_id="chief")
        entries = B.ledger(store)
        run_ids = [e["runId"] for e in entries if e["kind"] == "spend"]
        assert run_ids == [f"run-{i}" for i in range(9, -1, -1)]


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


class TestPlanConfig:
    def test_the_shipped_plans_load(self):
        plans = B.load_plans()
        assert "entry" in plans["plans"] and "mid" in plans["plans"]

    def test_plan_raises_for_an_unknown_key(self):
        with pytest.raises(ValueError, match="unknown plan"):
            B.plan("enterprise")

    def test_credit_top_up_raises_for_an_unknown_key(self):
        with pytest.raises(ValueError, match="unknown credit top-up"):
            B.credit_top_up("amazai_credits_9999")

    def test_credit_top_up_finds_a_real_one(self):
        row = B.credit_top_up("amazai_credits_50")
        assert row["priceUsd"] == 50.0


class TestStripeCustomerLink:
    def test_linking_lets_the_customer_be_resolved_back_to_the_owner(self, store, table):
        B.link_stripe_customer(store, "cus_123", table=table)
        assert B.owner_for_stripe_customer("cus_123", table=table) == store.owner_id

    def test_linking_also_stamps_the_billing_row(self, store, table):
        B.ensure_billing_row(store)
        B.link_stripe_customer(store, "cus_123", table=table)
        row = store.get(K_user_pk(store), "BILLING")
        assert row["stripeCustomerId"] == "cus_123"

    def test_an_unlinked_customer_resolves_to_nothing(self, table):
        assert B.owner_for_stripe_customer("cus_never_linked", table=table) is None

    def test_two_owners_linking_different_customers_do_not_collide(self, two_stores, table):
        store_a, store_b = two_stores
        B.link_stripe_customer(store_a, "cus_a", table=table)
        B.link_stripe_customer(store_b, "cus_b", table=table)
        assert B.owner_for_stripe_customer("cus_a", table=table) == store_a.owner_id
        assert B.owner_for_stripe_customer("cus_b", table=table) == store_b.owner_id


def K_user_pk(store):
    from amazai import keys as K
    return K.user_pk(store.owner_id)


class _FakeStripeClient:
    """Scripted in place of amazai.stripe_client.StripeClient -- these tests
    are about billing.py's own logic (which price, whose customer id, what
    gets stored), not the HTTP client, which test_stripe_client.py already
    covers on its own."""

    def __init__(self):
        self.checkout_calls: list[dict] = []
        self.portal_calls: list[dict] = []

    def create_checkout_session(self, **kwargs):
        self.checkout_calls.append(kwargs)
        return {"id": "cs_test_1", "url": "https://checkout.stripe.com/x"}

    def create_portal_session(self, **kwargs):
        self.portal_calls.append(kwargs)
        return {"id": "bps_1", "url": "https://billing.stripe.com/x"}


class TestStartCheckout:
    def test_a_plan_checkout_uses_the_plans_price_and_carries_its_key_in_metadata(
            self, store, monkeypatch):
        fake = _FakeStripeClient()
        monkeypatch.setattr(B.stripe_client, "StripeClient", lambda: fake)
        # The shipped config ships stripePriceId: null; a real price is what
        # scripts/stripe_setup.py fills in -- stand one up for this test.
        monkeypatch.setattr(B, "plan", lambda key: {"stripePriceId": "price_entry_x"})

        url = B.start_checkout(store, plan_key="entry",
                               success_url="https://x/ok", cancel_url="https://x/cancel")

        assert url == "https://checkout.stripe.com/x"
        call = fake.checkout_calls[0]
        assert call["mode"] == "subscription"
        assert call["price_id"] == "price_entry_x"
        assert call["owner_id"] == store.owner_id
        assert call["metadata"] == {"planKey": "entry"}

    def test_a_top_up_checkout_is_one_time_payment_mode(self, store, monkeypatch):
        fake = _FakeStripeClient()
        monkeypatch.setattr(B.stripe_client, "StripeClient", lambda: fake)
        monkeypatch.setattr(B, "credit_top_up", lambda key: {"stripePriceId": "price_credits_x"})

        B.start_checkout(store, top_up_key="amazai_credits_50",
                         success_url="https://x/ok", cancel_url="https://x/cancel")

        assert fake.checkout_calls[0]["mode"] == "payment"

    def test_an_existing_stripe_customer_is_reused(self, store, monkeypatch):
        fake = _FakeStripeClient()
        monkeypatch.setattr(B.stripe_client, "StripeClient", lambda: fake)
        monkeypatch.setattr(B, "plan", lambda key: {"stripePriceId": "price_x"})
        store.put({"pk": K_user_pk(store), "sk": B.BILLING_SK, "entity": "Billing",
                  "creditBalanceMicros": 0, "stripeCustomerId": "cus_existing"})

        B.start_checkout(store, plan_key="entry",
                         success_url="https://x", cancel_url="https://x")

        assert fake.checkout_calls[0]["customer_id"] == "cus_existing"

    def test_neither_plan_nor_top_up_is_a_clear_error(self, store):
        with pytest.raises(ValueError, match="exactly one"):
            B.start_checkout(store, success_url="https://x", cancel_url="https://x")

    def test_a_plan_with_no_stripe_price_yet_refuses_clearly(self, store):
        with pytest.raises(RuntimeError, match="stripe_setup.py"):
            B.start_checkout(store, plan_key="entry",
                             success_url="https://x", cancel_url="https://x")


class TestStartPortal:
    def test_returns_the_portal_url_for_an_existing_customer(self, store, monkeypatch):
        fake = _FakeStripeClient()
        monkeypatch.setattr(B.stripe_client, "StripeClient", lambda: fake)
        store.put({"pk": K_user_pk(store), "sk": B.BILLING_SK, "entity": "Billing",
                  "creditBalanceMicros": 0, "stripeCustomerId": "cus_existing"})

        url = B.start_portal(store, return_url="https://x/billing")

        assert url == "https://billing.stripe.com/x"
        assert fake.portal_calls[0]["customer_id"] == "cus_existing"

    def test_an_account_with_no_stripe_customer_yet_refuses_clearly(self, store):
        with pytest.raises(RuntimeError, match="subscribe first"):
            B.start_portal(store, return_url="https://x")


class TestWebhookDispatch:
    def test_an_unrecognized_event_type_is_a_quiet_no_op(self, table):
        result = B.handle_webhook_event({"type": "customer.created", "id": "evt_1"}, table=table)
        assert result == {"handled": False, "type": "customer.created"}

    def test_checkout_completed_for_a_top_up_grants_credit(self, store, table):
        B.ensure_billing_row(store)
        event = {"id": "evt_1", "type": "checkout.session.completed",
                 "data": {"object": {
                     "client_reference_id": store.owner_id, "customer": "cus_1",
                     "mode": "payment", "amount_total": 5000,
                 }}}
        result = B.handle_webhook_event(event, table=table)
        assert result["handled"] is True
        assert B.balance_usd(store) == pytest.approx(B.TRIAL_GRANT_USD + 50.0)
        assert B.owner_for_stripe_customer("cus_1", table=table) == store.owner_id

    def test_checkout_completed_for_a_subscription_sets_the_tier_without_double_granting(
            self, store, table):
        B.ensure_billing_row(store)
        before = B.balance_usd(store)
        event = {"id": "evt_2", "type": "checkout.session.completed",
                 "data": {"object": {
                     "client_reference_id": store.owner_id, "customer": "cus_2",
                     "mode": "subscription", "metadata": {"planKey": "entry"},
                 }}}
        B.handle_webhook_event(event, table=table)
        row = store.get(K_user_pk(store), B.BILLING_SK)
        assert row["tier"] == "entry"
        assert B.balance_usd(store) == before  # invoice.paid grants the period, not this

    def test_checkout_completed_replayed_does_not_double_grant_a_top_up(self, store, table):
        B.ensure_billing_row(store)
        event = {"id": "evt_3", "type": "checkout.session.completed",
                 "data": {"object": {
                     "client_reference_id": store.owner_id, "customer": "cus_3",
                     "mode": "payment", "amount_total": 5000,
                 }}}
        B.handle_webhook_event(event, table=table)
        B.handle_webhook_event(event, table=table)  # Stripe's at-least-once delivery
        assert B.balance_usd(store) == pytest.approx(B.TRIAL_GRANT_USD + 50.0)

    def test_invoice_paid_grants_the_renewal_to_the_linked_owner(self, store, table):
        B.ensure_billing_row(store)
        B.link_stripe_customer(store, "cus_4", table=table)
        event = {"id": "evt_4", "type": "invoice.paid",
                 "data": {"object": {"customer": "cus_4", "amount_paid": 2000}}}
        result = B.handle_webhook_event(event, table=table)
        assert result["handled"] is True
        assert B.balance_usd(store) == pytest.approx(B.TRIAL_GRANT_USD + 20.0)

    def test_invoice_paid_for_an_unlinked_customer_is_reported_not_raised(self, table):
        event = {"id": "evt_5", "type": "invoice.paid",
                 "data": {"object": {"customer": "cus_never_linked", "amount_paid": 2000}}}
        result = B.handle_webhook_event(event, table=table)
        assert result == {"handled": False, "reason": "unknown customer"}

    def test_subscription_deleted_marks_the_billing_row_canceled(self, store, table):
        B.ensure_billing_row(store)
        B.link_stripe_customer(store, "cus_6", table=table)
        event = {"id": "evt_6", "type": "customer.subscription.deleted",
                 "data": {"object": {"customer": "cus_6", "id": "sub_1"}}}
        B.handle_webhook_event(event, table=table)
        row = store.get(K_user_pk(store), B.BILLING_SK)
        assert row["subscriptionStatus"] == "canceled"
        assert row["stripeSubscriptionId"] == "sub_1"

    def test_subscription_updated_records_stripes_own_status(self, store, table):
        B.ensure_billing_row(store)
        B.link_stripe_customer(store, "cus_7", table=table)
        event = {"id": "evt_7", "type": "customer.subscription.updated",
                 "data": {"object": {"customer": "cus_7", "id": "sub_1",
                                     "status": "past_due"}}}
        B.handle_webhook_event(event, table=table)
        row = store.get(K_user_pk(store), B.BILLING_SK)
        assert row["subscriptionStatus"] == "past_due"
