"""Raw HTTP calls to Stripe's REST API.

No SDK: this codebase's Lambdas ship no third-party dependency beyond
boto3 (from a layer), matching `composio.py`'s own choice -- and for the
same reason, a bundled SDK is a supply-chain surface and a cold-start cost
for a handful of endpoints this module can call directly.

Stripe's API takes `application/x-www-form-urlencoded` bodies with bracket
notation for nested values (`line_items[0][price]=price_x`), unlike
Composio's plain JSON. `_encode_form` is the one place that flattening
happens; every other function in here just builds a plain nested dict.

Webhook signature verification needs no HTTP call at all -- see
`verify_webhook`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from amazai import secrets

API_BASE = "https://api.stripe.com/v1"
DEFAULT_TIMEOUT = 15

#: How far a webhook's own timestamp may drift from now and still verify.
#: What stops a captured, replayed request from verifying forever.
WEBHOOK_TOLERANCE_SECONDS = 300


class StripeError(RuntimeError):
    """A call to Stripe failed. Carries no credential material."""

    def __init__(self, message: str, *, status: int | None = None, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class SignatureVerificationError(RuntimeError):
    """A webhook's signature did not match, or its timestamp was stale."""


def _encode_form(params: dict, prefix: str = "") -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None:
            continue
        full_key = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            pairs.extend(_encode_form(value, full_key))
        elif isinstance(value, (list, tuple)):
            for i, item in enumerate(value):
                item_key = f"{full_key}[{i}]"
                if isinstance(item, dict):
                    pairs.extend(_encode_form(item, item_key))
                else:
                    pairs.append((item_key, str(item)))
        elif isinstance(value, bool):
            pairs.append((full_key, "true" if value else "false"))
        else:
            pairs.append((full_key, str(value)))
    return pairs


def _describe(status: int, payload: dict) -> str:
    err = payload.get("error") if isinstance(payload, dict) else {}
    message = (err or {}).get("message") or ""
    if status in (401, 403):
        return f"Stripe rejected the request ({status}). Check the amazai/stripe secret."
    return f"Stripe returned {status}: {message[:300] if message else 'no detail'}"


def _request(method: str, path: str, *, api_key: str, params: dict | None = None,
             timeout: int = DEFAULT_TIMEOUT) -> dict:
    url = f"{API_BASE}{path}"
    data = None
    if method == "GET" and params:
        url += "?" + urllib.parse.urlencode(_encode_form(params))
    elif params:
        data = urllib.parse.urlencode(_encode_form(params)).encode()

    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {api_key}")
    if data is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode() or "{}"
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()[:2000]
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"raw": raw}
        err = payload.get("error") if isinstance(payload, dict) else {}
        raise StripeError(_describe(exc.code, payload), status=exc.code,
                          code=(err or {}).get("code", "")) from exc
    except urllib.error.URLError as exc:
        raise StripeError(f"{method} could not reach Stripe: {exc.reason}") from exc


class StripeClient:
    """One client per Lambda container, same shape as `composio.Composio`:
    the secret key is read from Secrets Manager on first use and held only
    in this object."""

    def __init__(self, *, api_key: str | None = None, request=_request) -> None:
        self._api_key = api_key
        self._request = request

    def _key(self) -> str:
        return self._api_key or secrets.stripe_secret_key()

    def create_checkout_session(self, *, mode: str, price_id: str, owner_id: str,
                                success_url: str, cancel_url: str,
                                customer_id: str | None = None,
                                customer_email: str | None = None,
                                quantity: int = 1,
                                metadata: dict | None = None) -> dict:
        """`client_reference_id` is how the webhook later maps a Stripe
        event back to an AmazAI owner -- Stripe has no idea what an
        `ownerId` is, so this is the one place that mapping is established.

        `metadata` (e.g. `{"planKey": "entry"}`) is echoed back verbatim on
        the session object in the `checkout.session.completed` webhook, with
        no follow-up API call needed to learn which plan was purchased --
        Stripe's own documented way to carry an integration's own data
        through a session, not a workaround.
        """
        params: dict = {
            "mode": mode,
            "line_items": [{"price": price_id, "quantity": quantity}],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "client_reference_id": owner_id,
        }
        if customer_id:
            params["customer"] = customer_id
        elif customer_email:
            params["customer_email"] = customer_email
        if metadata:
            params["metadata"] = metadata
        return self._request("POST", "/checkout/sessions", api_key=self._key(), params=params)

    def create_portal_session(self, *, customer_id: str, return_url: str) -> dict:
        return self._request("POST", "/billing_portal/sessions", api_key=self._key(),
                             params={"customer": customer_id, "return_url": return_url})

    def create_product(self, *, name: str, description: str = "") -> dict:
        params = {"name": name}
        if description:
            params["description"] = description
        return self._request("POST", "/products", api_key=self._key(), params=params)

    def create_price(self, *, product_id: str, unit_amount_cents: int, currency: str = "usd",
                     recurring_interval: str | None = None, lookup_key: str | None = None) -> dict:
        params: dict = {"product": product_id, "unit_amount": unit_amount_cents,
                        "currency": currency}
        if recurring_interval:
            params["recurring"] = {"interval": recurring_interval}
        if lookup_key:
            params["lookup_key"] = lookup_key
        return self._request("POST", "/prices", api_key=self._key(), params=params)

    def find_price_by_lookup_key(self, lookup_key: str) -> dict | None:
        resp = self._request("GET", "/prices", api_key=self._key(),
                             params={"lookup_keys": [lookup_key], "active": True})
        items = resp.get("data") or []
        return items[0] if items else None


def verify_webhook(payload: bytes, sig_header: str, webhook_secret: str) -> dict:
    """Verify a Stripe webhook's signature and return the parsed event.

    Stripe's own algorithm: HMAC-SHA256 over `"<timestamp>.<payload>"`,
    keyed on the webhook's signing secret, compared against the `v1=` value
    in the `Stripe-Signature` header -- constant-time, since this is exactly
    the kind of comparison a timing side-channel could otherwise leak.
    """
    if not sig_header:
        raise SignatureVerificationError("missing Stripe-Signature header")
    parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
    timestamp, signature = parts.get("t"), parts.get("v1")
    if not timestamp or not signature:
        raise SignatureVerificationError("malformed Stripe-Signature header")

    try:
        if abs(time.time() - int(timestamp)) > WEBHOOK_TOLERANCE_SECONDS:
            raise SignatureVerificationError("webhook timestamp outside tolerance")
    except ValueError as exc:
        raise SignatureVerificationError("malformed timestamp") from exc

    signed_payload = timestamp.encode() + b"." + payload
    expected = hmac.new(webhook_secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise SignatureVerificationError("signature mismatch")

    try:
        return json.loads(payload)
    except ValueError as exc:
        raise SignatureVerificationError("payload is not valid JSON") from exc
