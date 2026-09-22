"""`amazai.stripe_client`, one layer below the network.

`StripeClient` takes its HTTP function as an argument (same seam
`composio.Composio` uses), so these tests exercise real URL/form building,
response handling and error classification against a scripted transport --
only the network hop is faked. Webhook verification needs no transport at
all: it is pure HMAC over bytes already in hand.
"""

import hashlib
import hmac
import json
import time

import pytest

from amazai import stripe_client as SC


class FakeTransport:
    """Records every call; answers with whatever this test scripted."""

    def __init__(self, responses: dict | None = None):
        self.responses = responses or {}
        self.calls: list[dict] = []

    def __call__(self, method, path, *, api_key, params=None, timeout=15):  # noqa: ARG002
        self.calls.append({"method": method, "path": path, "api_key": api_key,
                           "params": params})
        key = (method, path)
        if key in self.responses:
            resp = self.responses[key]
            if isinstance(resp, Exception):
                raise resp
            return resp
        raise AssertionError(f"unscripted call: {method} {path}")


def client(responses=None):
    transport = FakeTransport(responses)
    return SC.StripeClient(api_key="sk_test_not_real", request=transport), transport


class TestFormEncoding:
    def test_flat_params_encode_as_is(self):
        assert dict(SC._encode_form({"mode": "subscription"})) == {"mode": "subscription"}

    def test_a_list_of_dicts_gets_bracketed_indices(self):
        pairs = dict(SC._encode_form({"line_items": [{"price": "price_x", "quantity": 1}]}))
        assert pairs == {"line_items[0][price]": "price_x", "line_items[0][quantity]": "1"}

    def test_a_nested_dict_gets_one_level_of_brackets(self):
        pairs = dict(SC._encode_form({"recurring": {"interval": "month"}}))
        assert pairs == {"recurring[interval]": "month"}

    def test_none_values_are_omitted(self):
        pairs = dict(SC._encode_form({"customer": None, "mode": "payment"}))
        assert pairs == {"mode": "payment"}

    def test_booleans_encode_as_stripe_expects(self):
        pairs = dict(SC._encode_form({"active": True, "archived": False}))
        assert pairs == {"active": "true", "archived": "false"}


class TestCreateCheckoutSession:
    def test_sends_the_client_reference_id_for_later_webhook_mapping(self):
        c, transport = client({
            ("POST", "/checkout/sessions"): {"id": "cs_test_1", "url": "https://checkout.stripe.com/x"},
        })
        result = c.create_checkout_session(
            mode="subscription", price_id="price_entry", owner_id="owner-a",
            success_url="https://amazai.co/billing?ok=1", cancel_url="https://amazai.co/billing")
        assert result["id"] == "cs_test_1"
        call = transport.calls[0]
        assert call["params"]["client_reference_id"] == "owner-a"
        assert call["params"]["line_items"] == [{"price": "price_entry", "quantity": 1}]

    def test_an_existing_customer_is_reused_over_an_email(self):
        c, transport = client({("POST", "/checkout/sessions"): {"id": "cs_test_1"}})
        c.create_checkout_session(
            mode="payment", price_id="price_credits", owner_id="owner-a",
            success_url="https://x", cancel_url="https://x",
            customer_id="cus_123", customer_email="ignored@example.com")
        params = transport.calls[0]["params"]
        assert params["customer"] == "cus_123"
        assert "customer_email" not in params

    def test_metadata_is_passed_through_for_the_webhook_to_read_back(self):
        c, transport = client({("POST", "/checkout/sessions"): {"id": "cs_test_1"}})
        c.create_checkout_session(
            mode="subscription", price_id="price_entry", owner_id="owner-a",
            success_url="https://x", cancel_url="https://x",
            metadata={"planKey": "entry"})
        assert transport.calls[0]["params"]["metadata"] == {"planKey": "entry"}

    def test_a_rejected_key_raises_a_readable_error(self):
        c, _ = client({("POST", "/checkout/sessions"):
                       SC.StripeError("Stripe rejected the request (401).", status=401)})
        with pytest.raises(SC.StripeError, match="401"):
            c.create_checkout_session(mode="subscription", price_id="price_x", owner_id="o",
                                      success_url="https://x", cancel_url="https://x")


class TestCreatePortalSession:
    def test_sends_the_customer_and_return_url(self):
        c, transport = client({
            ("POST", "/billing_portal/sessions"): {"id": "bps_1", "url": "https://billing.stripe.com/x"},
        })
        result = c.create_portal_session(customer_id="cus_123", return_url="https://amazai.co/billing")
        assert result["url"].startswith("https://billing.stripe.com")
        assert transport.calls[0]["params"] == {"customer": "cus_123",
                                                "return_url": "https://amazai.co/billing"}


class TestProductsAndPrices:
    def test_create_product_sends_the_name(self):
        c, transport = client({("POST", "/products"): {"id": "prod_1"}})
        c.create_product(name="AmazAI Entry")
        assert transport.calls[0]["params"] == {"name": "AmazAI Entry"}

    def test_create_price_encodes_the_recurring_interval(self):
        c, transport = client({("POST", "/prices"): {"id": "price_1"}})
        c.create_price(product_id="prod_1", unit_amount_cents=2000, recurring_interval="month",
                       lookup_key="entry_monthly")
        params = transport.calls[0]["params"]
        assert params["recurring"] == {"interval": "month"}
        assert params["lookup_key"] == "entry_monthly"
        assert params["unit_amount"] == 2000

    def test_find_price_by_lookup_key_returns_none_when_absent(self):
        c, _ = client({("GET", "/prices"): {"data": []}})
        assert c.find_price_by_lookup_key("nope") is None

    def test_find_price_by_lookup_key_returns_the_first_match(self):
        c, _ = client({("GET", "/prices"): {"data": [{"id": "price_1"}, {"id": "price_2"}]}})
        assert c.find_price_by_lookup_key("entry_monthly")["id"] == "price_1"


class TestVerifyWebhook:
    def _sign(self, payload: bytes, secret: str, *, at: float | None = None) -> str:
        ts = str(int(at if at is not None else time.time()))
        signed = ts.encode() + b"." + payload
        sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
        return f"t={ts},v1={sig}"

    def test_a_correctly_signed_payload_verifies_and_parses(self):
        payload = json.dumps({"type": "checkout.session.completed", "id": "evt_1"}).encode()
        header = self._sign(payload, "whsec_test")
        event = SC.verify_webhook(payload, header, "whsec_test")
        assert event["id"] == "evt_1"

    def test_the_wrong_secret_is_refused(self):
        payload = json.dumps({"id": "evt_1"}).encode()
        header = self._sign(payload, "whsec_correct")
        with pytest.raises(SC.SignatureVerificationError, match="mismatch"):
            SC.verify_webhook(payload, header, "whsec_wrong")

    def test_a_tampered_payload_is_refused(self):
        payload = json.dumps({"id": "evt_1", "amount": 100}).encode()
        header = self._sign(payload, "whsec_test")
        tampered = json.dumps({"id": "evt_1", "amount": 999999}).encode()
        with pytest.raises(SC.SignatureVerificationError, match="mismatch"):
            SC.verify_webhook(tampered, header, "whsec_test")

    def test_a_stale_timestamp_is_refused_even_with_a_correct_signature(self):
        payload = json.dumps({"id": "evt_1"}).encode()
        header = self._sign(payload, "whsec_test", at=time.time() - 3600)
        with pytest.raises(SC.SignatureVerificationError, match="tolerance"):
            SC.verify_webhook(payload, header, "whsec_test")

    def test_a_missing_header_is_refused(self):
        payload = json.dumps({"id": "evt_1"}).encode()
        with pytest.raises(SC.SignatureVerificationError, match="missing"):
            SC.verify_webhook(payload, "", "whsec_test")

    def test_a_malformed_header_is_refused(self):
        payload = json.dumps({"id": "evt_1"}).encode()
        with pytest.raises(SC.SignatureVerificationError, match="malformed"):
            SC.verify_webhook(payload, "not-a-real-header", "whsec_test")
