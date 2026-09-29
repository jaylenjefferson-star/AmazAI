"""The billing routes, driven through the real Lambda handler.

Checkout/portal are tested against a stubbed billing.start_checkout/
start_portal -- billing.py's own logic and the Stripe HTTP client are
already covered in test_billing.py and test_stripe_client.py. What only the
handler can show: origin validation, error-to-status-code mapping, and (for
the webhook) that a request with no Auth0 token at all still reaches
something, verified purely by its Stripe signature.
"""

import hashlib
import hmac
import json
import time

import pytest

from amazai import billing, keys as K, stripe_client
from amazai.store import Store

import handlers.api as api

from tests.test_agents_api import api_table  # noqa: F401

ORIGIN = "https://amazai.co"


def event(method, path, body=None, *, headers=None, sub="owner-a"):
    h = {"origin": ORIGIN}
    h.update(headers or {})
    return {
        "requestContext": {
            "http": {"method": method},
            "authorizer": {"jwt": {"claims": {"sub": sub, "custom:orgId": "org-1"}}},
        },
        "rawPath": path,
        "headers": h,
        "queryStringParameters": None,
        "body": json.dumps(body) if body is not None else None,
    }


def call(method, path, body=None, **kw):
    resp = api.handler(event(method, path, body, **kw), None)
    return resp["statusCode"], json.loads(resp["body"]) if resp.get("body") else None


class TestGetBillingPlans:
    def test_returns_the_shipped_plan_config(self, api_table):
        status, body = call("GET", "/billing/plans")
        assert status == 200
        for key in ("explore", "personal", "personal_plus", "pro", "power"):
            assert key in body["plans"]


class TestGetBilling:
    def test_a_new_account_is_registration_incomplete(self, api_table):
        status, body = call("GET", "/billing")
        assert status == 200
        assert body["tier"] == "trial"
        assert body["registrationIncomplete"] is True
        assert body["hasStripeCustomer"] is False
        assert body["balanceUsd"] == billing.TRIAL_GRANT_USD


class TestConfirmExplore:
    def test_confirm_sets_explore_and_keeps_the_trial_balance(self, api_table):
        status, body = call("POST", "/billing/explore", {})
        assert status == 200
        assert body["tier"] == "explore"
        assert body["registrationIncomplete"] is False
        assert body["hasStripeCustomer"] is False
        assert body["balanceUsd"] == billing.TRIAL_GRANT_USD


class TestPostCheckout:
    def test_signup_checkout_returns_to_the_plan_picker(self, api_table, monkeypatch):
        seen = {}

        def capture(store, **kw):
            seen.update(kw)
            return "https://checkout.stripe.com/c/pay_x"

        monkeypatch.setattr(api.billing, "start_checkout", capture)
        status, body = call("POST", "/billing/checkout",
                            {"planKey": "personal", "purpose": "signup"})
        assert status == 200
        assert body["url"] == "https://checkout.stripe.com/c/pay_x"
        assert seen["success_url"] == f"{ORIGIN}/plans/success"
        assert seen["cancel_url"] == f"{ORIGIN}/plans?checkout=cancelled"
        assert seen["plan_key"] == "personal"

    def test_billing_checkout_still_returns_to_billing(self, api_table, monkeypatch):
        seen = {}

        def capture(store, **kw):
            seen.update(kw)
            return "https://checkout.stripe.com/c/pay_x"

        monkeypatch.setattr(api.billing, "start_checkout", capture)
        status, _body = call("POST", "/billing/checkout", {"planKey": "personal"})
        assert status == 200
        assert seen["success_url"] == f"{ORIGIN}/billing?checkout=success"
        assert seen["cancel_url"] == f"{ORIGIN}/billing?checkout=cancelled"

    def test_an_unknown_purpose_is_refused(self, api_table, monkeypatch):
        monkeypatch.setattr(api.billing, "start_checkout", lambda store, **kw: "https://x")
        status, body = call("POST", "/billing/checkout",
                            {"planKey": "personal", "purpose": "https://evil.example"})
        assert status == 400
        assert body["error"] == "invalid_request"

    def test_signup_cannot_start_a_credit_top_up(self, api_table, monkeypatch):
        monkeypatch.setattr(api.billing, "start_checkout", lambda store, **kw: "https://x")
        status, body = call("POST", "/billing/checkout",
                            {"topUpKey": "amazai_credits_50", "purpose": "signup"})
        assert status == 400
        assert "top-up" in body["detail"]

    def test_explore_checkout_is_refused(self, api_table):
        status, body = call("POST", "/billing/checkout", {"planKey": "explore"})
        assert status == 400
        assert "card" in body["detail"]

    def test_a_recognized_origin_starts_a_checkout(self, api_table, monkeypatch):
        monkeypatch.setattr(api.billing, "start_checkout",
                            lambda store, **kw: "https://checkout.stripe.com/x")
        status, body = call("POST", "/billing/checkout", {"planKey": "entry"})
        assert status == 200
        assert body["url"] == "https://checkout.stripe.com/x"

    def test_an_unrecognized_origin_is_refused(self, api_table, monkeypatch):
        monkeypatch.setattr(api.billing, "start_checkout",
                            lambda store, **kw: "https://checkout.stripe.com/x")
        status, body = call("POST", "/billing/checkout", {"planKey": "entry"},
                            headers={"origin": "https://evil.example.com"})
        assert status == 400

    def test_a_plan_with_no_stripe_price_yet_is_409_not_500(self, api_table, monkeypatch):
        def boom(store, **kw):
            raise RuntimeError("no Stripe price yet; run scripts/stripe_setup.py")
        monkeypatch.setattr(api.billing, "start_checkout", boom)
        status, body = call("POST", "/billing/checkout", {"planKey": "entry"})
        assert status == 409
        assert body["error"] == "not_purchasable"

    def test_neither_plan_nor_top_up_is_400(self, api_table, monkeypatch):
        def boom(store, **kw):
            raise ValueError("exactly one of plan_key or top_up_key is required")
        monkeypatch.setattr(api.billing, "start_checkout", boom)
        status, body = call("POST", "/billing/checkout", {})
        assert status == 400

    def test_stripe_being_unreachable_is_502_not_500(self, api_table, monkeypatch):
        def boom(store, **kw):
            raise stripe_client.StripeError("could not reach Stripe")
        monkeypatch.setattr(api.billing, "start_checkout", boom)
        status, body = call("POST", "/billing/checkout", {"planKey": "entry"})
        assert status == 502


class TestPostPortal:
    def test_a_recognized_origin_starts_a_portal_session(self, api_table, monkeypatch):
        monkeypatch.setattr(api.billing, "start_portal",
                            lambda store, **kw: "https://billing.stripe.com/x")
        status, body = call("POST", "/billing/portal")
        assert status == 200
        assert body["url"] == "https://billing.stripe.com/x"

    def test_no_subscription_yet_is_409_not_500(self, api_table, monkeypatch):
        def boom(store, **kw):
            raise RuntimeError("this account has no Stripe customer yet; subscribe first")
        monkeypatch.setattr(api.billing, "start_portal", boom)
        status, body = call("POST", "/billing/portal")
        assert status == 409
        assert body["error"] == "no_subscription"


class TestWebhook:
    SECRET = "whsec_test_not_real"

    def _sign(self, payload: bytes) -> str:
        ts = str(int(time.time()))
        signed = ts.encode() + b"." + payload
        sig = hmac.new(self.SECRET.encode(), signed, hashlib.sha256).hexdigest()
        return f"t={ts},v1={sig}"

    def _webhook_event(self, payload: bytes, *, signed=True):
        return {
            "requestContext": {"http": {"method": "POST"}},
            "rawPath": "/billing/webhook",
            "headers": {"stripe-signature": self._sign(payload) if signed else ""},
            "body": payload.decode(),
        }

    def test_a_correctly_signed_event_is_dispatched(self, api_table, monkeypatch):
        monkeypatch.setattr(api.secrets, "stripe_webhook_secret", lambda: self.SECRET)
        dispatched = {}
        monkeypatch.setattr(api.billing, "handle_webhook_event",
                            lambda ev: dispatched.setdefault("event", ev) or {"handled": True})

        payload = json.dumps({"id": "evt_1", "type": "checkout.session.completed"}).encode()
        resp = api.handler(self._webhook_event(payload), None)

        assert resp["statusCode"] == 200
        assert dispatched["event"]["id"] == "evt_1"

    def test_a_bad_signature_is_refused_before_dispatch(self, api_table, monkeypatch):
        monkeypatch.setattr(api.secrets, "stripe_webhook_secret", lambda: self.SECRET)
        called = []
        monkeypatch.setattr(api.billing, "handle_webhook_event",
                            lambda ev: called.append(ev))

        payload = json.dumps({"id": "evt_1", "type": "checkout.session.completed"}).encode()
        resp = api.handler(self._webhook_event(payload, signed=False), None)

        assert resp["statusCode"] == 400
        assert called == []

    def test_the_secret_not_being_configured_yet_is_503_not_500(self, api_table, monkeypatch):
        def boom():
            raise RuntimeError("could not read secret 'amazai/stripe'")
        monkeypatch.setattr(api.secrets, "stripe_webhook_secret", boom)

        payload = json.dumps({"id": "evt_1", "type": "checkout.session.completed"}).encode()
        resp = api.handler(self._webhook_event(payload), None)

        assert resp["statusCode"] == 503

    def test_a_processing_failure_is_500_so_stripe_retries(self, api_table, monkeypatch):
        monkeypatch.setattr(api.secrets, "stripe_webhook_secret", lambda: self.SECRET)
        def boom(ev):
            raise RuntimeError("dynamodb blip")
        monkeypatch.setattr(api.billing, "handle_webhook_event", boom)

        payload = json.dumps({"id": "evt_1", "type": "checkout.session.completed"}).encode()
        resp = api.handler(self._webhook_event(payload), None)

        assert resp["statusCode"] == 500

    def test_the_webhook_route_needs_no_bearer_token_at_all(self, api_table, monkeypatch):
        """The whole point of signature verification: Stripe never sends an
        Authorization header, and this route must still work."""
        monkeypatch.setattr(api.secrets, "stripe_webhook_secret", lambda: self.SECRET)
        monkeypatch.setattr(api.billing, "handle_webhook_event", lambda ev: {"handled": True})

        payload = json.dumps({"id": "evt_1", "type": "checkout.session.completed"}).encode()
        raw_event = self._webhook_event(payload)
        assert "authorization" not in raw_event["headers"]

        resp = api.handler(raw_event, None)
        assert resp["statusCode"] == 200


class TestTheGatewayItselfExemptsTheWebhook:
    """The handler-level tests above proved the *code's* bypass works -- but
    api.handler is never actually reached unless API Gateway's own route
    resolves without its Auth0 JWT authorizer first. A live smoke test
    against the deployed endpoint caught exactly this: the catch-all
    `/{proxy+}` route attaches the authorizer to every path, including
    /billing/webhook, and API Gateway's own 401 (`{"message":"Unauthorized"}`,
    distinct in shape from this handler's `{"error": ...}`) never reaches
    handler code at all. This is the regression guard for the fix -- a
    dedicated, unauthenticated route registered for exactly this path."""

    def test_the_webhook_route_has_no_authorizer(self):
        from pathlib import Path
        stack = (Path(__file__).resolve().parents[1] / "infra/lib/amazai-stack.ts").read_text()
        start = stack.index("path: '/billing/webhook'")
        block = stack[start:stack.index("});", start)]
        assert "authorizer" not in block
