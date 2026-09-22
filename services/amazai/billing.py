"""Account-level credits: the balance every run's actual cost draws down.

One credit is one dollar of actual spend -- the same `RunCost.total_usd`
`cost.py` already computes from real model/runtime/connector usage. This is
not a second pricing table; it is the account-wide ceiling "as long as they
have credits" describes, layered on top of -- never replacing -- each
agent's own per-run/per-month `Budget` (`cost.py`), which still stops one
Bot from looping a whole account's balance away in a single run.

Stored in micro-dollar integers (`amazai.usage.MICRO`), the same convention
`usage.py` already uses for exact money arithmetic. `Store.increment` only
performs an integer DynamoDB `ADD`; a dollars-and-cents balance drifting
under repeated float addition is exactly the kind of bug a ledger cannot
afford, and `Store.increment`'s linearizable per-row ADD is also what makes
two runs finishing at the same instant both debit correctly -- the same
reason `handoffs.py`'s fan-in counter uses it instead of a read-modify-write.

Coarse by design. This checks *whether* an account has anything left before
a run starts, not how much a run may spend mid-flight -- that finer ceiling
already exists (`cost.check`'s per-run/per-month Budget, re-checked between
tool rounds). A run that starts with $0.01 left is allowed to start; the
existing per-run ceiling still stops it from running far past zero. Metering
spend mid-run against the account balance itself is deliberately out of
scope for this pass -- it would mean threading a check into the tool-round
loop's hot path for a precision this MVP does not need.
"""

from __future__ import annotations

import json
from pathlib import Path

import boto3

from amazai import keys as K, stripe_client
from amazai.store import Store, TABLE_NAME, now_iso, ordered_suffix
from amazai.usage import MICRO

BILLING_SK = "BILLING"

#: A one-time grant on signup, no clock -- the trial ends when it is spent,
#: not on a timer. See api._warm_account_harness for the sibling signup hook
#: this rides alongside.
TRIAL_GRANT_USD = 5.0

TIER_TRIAL = "trial"

#: Every Stripe customer this platform has ever linked to an owner, in one
#: fixed, reserved partition -- the one place this module steps outside its
#: own owner's Store, and for the same reason identity's DB-backed allowlist
#: and store.discover_owner_ids do: a webhook event carrying only a Stripe
#: `customer` id has no owner to open a Store for until this is read first.
PLATFORM_STRIPE_CUSTOMERS_PK = "PLATFORM#STRIPECUSTOMERS"

_PLANS_PATH = Path(__file__).parent / "billing_plans.json"


def _pk(store: Store) -> str:
    return K.user_pk(store.owner_id)


def _to_micros(usd: float) -> int:
    return round(usd * MICRO)


def _to_usd(micros) -> float:
    return round(int(micros) / MICRO, 6)


def ensure_billing_row(store: Store) -> dict:
    """The owner's one billing row, creating it (with the free trial grant)
    on first sight. Idempotent, and safe to call on every authenticated
    request -- mirrors `identity.ensure_user`'s own shape, and is called
    right alongside it so a brand-new signup already has a balance before
    anything ever needs to check one.
    """
    pk = _pk(store)
    existing = store.try_get(pk, BILLING_SK)
    if existing:
        return existing

    row = store.put({
        "pk": pk, "sk": BILLING_SK,
        "entity": "Billing",
        "creditBalanceMicros": _to_micros(TRIAL_GRANT_USD),
        "tier": TIER_TRIAL,
        "stripeCustomerId": None,
        "stripeSubscriptionId": None,
        "subscriptionStatus": None,
        "createdAt": now_iso(), "updatedAt": now_iso(),
    })
    _ledger_entry(store, kind="trial_grant", amount_usd=TRIAL_GRANT_USD,
                  balance_after_usd=TRIAL_GRANT_USD,
                  detail="free trial credit on signup")
    return row


def balance_usd(store: Store) -> float:
    row = store.try_get(_pk(store), BILLING_SK)
    if not row:
        return 0.0
    return _to_usd(row.get("creditBalanceMicros", 0))


def has_credit(store: Store) -> bool:
    """The account-wide pre-flight gate: is there anything left to spend?

    An account with no billing row at all is open, the same convention
    `identity.assert_owner` uses for an unconfigured allowlist: nothing to
    gate against is not the same fact as a balance of zero. Every real
    account gets a row before this can ever be reached -- `api.handler`
    calls `ensure_billing_row` on every authenticated request, ahead of
    anything that could start a run -- so in production this branch means
    "never provisioned for credits" (local/dev), never "a real account
    that happens to have none. Once a row exists, it is authoritative:
    zero or negative correctly refuses.

    See the module docstring for why this is a coarse yes/no, not a
    mid-run metering check.
    """
    row = store.try_get(_pk(store), BILLING_SK)
    if not row:
        return True
    return _to_usd(row.get("creditBalanceMicros", 0)) > 0.0


def spend(store: Store, amount_usd: float, *, run_id: str, agent_id: str) -> float:
    """Debit the account for one run's actual, already-computed cost.
    Returns the balance after. A non-positive amount is a no-op: nothing a
    run reports as free should touch the ledger.

    An account with no billing row is left alone, the same "never gated"
    story `has_credit` tells: `Store.increment`'s ADD is conditioned on the
    row already existing (see its own ownerId check), so incrementing a row
    that was never created would raise `Conflict` -- and this runs right
    after a run finishes, from code that must not fail because a ledger
    entry could not be written for an account nothing ever gated.
    """
    if amount_usd <= 0:
        return balance_usd(store)
    if not store.try_get(_pk(store), BILLING_SK):
        return 0.0
    new_micros = store.increment(_pk(store), BILLING_SK, "creditBalanceMicros",
                                 -_to_micros(amount_usd))
    _ledger_entry(store, kind="spend", amount_usd=-amount_usd,
                  balance_after_usd=_to_usd(new_micros),
                  detail=f"run {run_id}", run_id=run_id, agent_id=agent_id)
    return _to_usd(new_micros)


def grant(store: Store, amount_usd: float, *, kind: str, detail: str = "",
          stripe_event_id: str | None = None) -> float:
    """Credit the account -- a subscription's periodic renewal or a one-time
    top-up purchase. `stripe_event_id`, when given, makes the grant
    idempotent against Stripe's at-least-once webhook delivery: the same
    event landing twice must not double the balance.

    Unlike `spend`, a missing billing row is created rather than skipped: a
    real payment's credit must never be silently dropped because the row
    happened not to exist yet.
    """
    if stripe_event_id and _already_applied(store, stripe_event_id):
        return balance_usd(store)
    ensure_billing_row(store)
    new_micros = store.increment(_pk(store), BILLING_SK, "creditBalanceMicros",
                                 _to_micros(amount_usd))
    _ledger_entry(store, kind=kind, amount_usd=amount_usd,
                  balance_after_usd=_to_usd(new_micros), detail=detail,
                  stripe_event_id=stripe_event_id)
    return _to_usd(new_micros)


def _already_applied(store: Store, stripe_event_id: str) -> bool:
    return store.try_get(_pk(store), f"STRIPEEVENT#{stripe_event_id}") is not None


def _ledger_entry(store: Store, *, kind: str, amount_usd: float,
                  balance_after_usd: float, detail: str = "", run_id: str = "",
                  agent_id: str = "", stripe_event_id: str | None = None) -> dict:
    entry = {
        "pk": _pk(store), "sk": f"CREDITLEDGER#{now_iso()}#{ordered_suffix()}",
        "entity": "CreditLedgerEntry",
        "kind": kind, "amountUsd": round(amount_usd, 6),
        "balanceAfterUsd": balance_after_usd,
        "detail": detail, "runId": run_id, "agentId": agent_id,
        "createdAt": now_iso(),
    }
    store.put(entry)
    if stripe_event_id:
        # A second marker row rather than a field on the entry above: this is
        # what `_already_applied` probes, and a dedicated key means a replayed
        # webhook is one `try_get`, not a query over every ledger entry.
        store.put({"pk": _pk(store), "sk": f"STRIPEEVENT#{stripe_event_id}",
                  "entity": "StripeEventMarker", "appliedAt": now_iso()})
    return entry


def ledger(store: Store, *, limit: int = 100) -> list[dict]:
    """Most recent entries first -- what a billing history screen reads."""
    return store.query(_pk(store), sk_prefix="CREDITLEDGER#", limit=limit,
                       ascending=False)


# --- plan config -------------------------------------------------------------

def load_plans() -> dict:
    """Tier and credit-top-up config, read fresh on every call -- an
    operator's edit, or `scripts/stripe_setup.py`'s own rewrite of
    `stripePriceId`, takes effect without a redeploy of anything but this
    one small file."""
    return json.loads(_PLANS_PATH.read_text())


def plan(key: str) -> dict:
    plans = load_plans()["plans"]
    if key not in plans:
        raise ValueError(f"unknown plan {key!r}")
    return plans[key]


def credit_top_up(lookup_key: str) -> dict:
    for row in load_plans()["creditTopUps"]:
        if row["lookupKey"] == lookup_key:
            return row
    raise ValueError(f"unknown credit top-up {lookup_key!r}")


# --- Stripe: the customer <-> owner link --------------------------------------

def _raw_table(table=None):
    return table or boto3.resource("dynamodb").Table(TABLE_NAME)


def link_stripe_customer(store: Store, customer_id: str, *, table=None) -> None:
    """Record which owner a Stripe customer belongs to, so a later webhook
    event carrying only `customer` (a renewal, a cancellation) can still be
    resolved to the right Store. Called once, the moment a checkout first
    completes for this owner (`_on_checkout_completed`).

    `ensure_billing_row` first, same defensive reasoning as `grant`: in the
    real flow `start_checkout` always creates the row before a checkout can
    even begin, but this must not assume its caller got that sequencing
    right -- linking a real paying customer's row is not something to skip
    because a row happened not to exist yet.
    """
    ensure_billing_row(store)
    _raw_table(table).put_item(Item={
        "pk": PLATFORM_STRIPE_CUSTOMERS_PK, "sk": customer_id,
        "entity": "StripeCustomerLink", "ownerId": store.owner_id,
        "linkedAt": now_iso(),
    })
    store.update(_pk(store), BILLING_SK, {"stripeCustomerId": customer_id})


def owner_for_stripe_customer(customer_id: str, *, table=None) -> str | None:
    item = _raw_table(table).get_item(
        Key={"pk": PLATFORM_STRIPE_CUSTOMERS_PK, "sk": customer_id}).get("Item")
    return item.get("ownerId") if item else None


def _store_for_customer(customer_id: str, *, table=None) -> Store | None:
    owner_id = owner_for_stripe_customer(customer_id, table=table)
    return Store(owner_id, table=table) if owner_id else None


# --- Stripe: starting a checkout or a portal session --------------------------

def start_checkout(store: Store, *, plan_key: str | None = None,
                   top_up_key: str | None = None, success_url: str,
                   cancel_url: str, customer_email: str | None = None) -> str:
    """Return a Stripe Checkout URL for a subscription plan or a one-time
    credit top-up -- exactly one of `plan_key`/`top_up_key` is given."""
    if plan_key:
        row, mode, metadata = plan(plan_key), "subscription", {"planKey": plan_key}
    elif top_up_key:
        row, mode, metadata = credit_top_up(top_up_key), "payment", {"topUpKey": top_up_key}
    else:
        raise ValueError("exactly one of plan_key or top_up_key is required")

    price_id = row.get("stripePriceId")
    if not price_id:
        raise RuntimeError(
            f"{(plan_key or top_up_key)!r} has no Stripe price yet; "
            "run scripts/stripe_setup.py against this account first")

    billing_row = ensure_billing_row(store)
    session = stripe_client.StripeClient().create_checkout_session(
        mode=mode, price_id=price_id, owner_id=store.owner_id,
        success_url=success_url, cancel_url=cancel_url,
        customer_id=billing_row.get("stripeCustomerId"),
        customer_email=customer_email, metadata=metadata)
    return session["url"]


def start_portal(store: Store, *, return_url: str) -> str:
    row = ensure_billing_row(store)
    customer_id = row.get("stripeCustomerId")
    if not customer_id:
        raise RuntimeError("this account has no Stripe customer yet; subscribe first")
    session = stripe_client.StripeClient().create_portal_session(
        customer_id=customer_id, return_url=return_url)
    return session["url"]


# --- Stripe: webhook events -----------------------------------------------

def handle_webhook_event(event: dict, *, table=None) -> dict:
    """Route one already-signature-verified Stripe event to its effect.

    Never raises for an event type this doesn't act on -- Stripe sends many
    more event types than any one integration needs to handle, and an
    unrecognized type is not a fault, just nothing to do.
    """
    kind = event.get("type", "")
    data = (event.get("data") or {}).get("object") or {}
    event_id = event.get("id", "")

    if kind == "checkout.session.completed":
        return _on_checkout_completed(data, event_id, table=table)
    if kind == "invoice.paid":
        return _on_invoice_paid(data, event_id, table=table)
    if kind in ("customer.subscription.updated", "customer.subscription.deleted"):
        return _on_subscription_changed(data, kind, table=table)
    return {"handled": False, "type": kind}


def _on_checkout_completed(session: dict, event_id: str, *, table=None) -> dict:
    owner_id = session.get("client_reference_id")
    customer_id = session.get("customer")
    if not owner_id or not customer_id:
        return {"handled": False, "reason": "missing client_reference_id or customer"}

    store = Store(owner_id, table=table)
    link_stripe_customer(store, customer_id, table=table)

    mode = session.get("mode")
    amount_total = (session.get("amount_total") or 0) / 100  # Stripe cents -> dollars
    if mode == "payment":
        grant(store, amount_total, kind="purchase", detail="credit top-up",
             stripe_event_id=event_id)
    elif mode == "subscription":
        # The subscription's own renewal grant lands separately via
        # invoice.paid -- Stripe fires that for every period, including this
        # first one, so granting here too would double-credit it. This only
        # records which tier was bought, read back from the metadata this
        # checkout session was created with (see start_checkout).
        plan_key = (session.get("metadata") or {}).get("planKey")
        if plan_key:
            store.update(_pk(store), BILLING_SK, {"tier": plan_key})
    return {"handled": True, "ownerId": owner_id}


def _on_invoice_paid(invoice: dict, event_id: str, *, table=None) -> dict:
    customer_id = invoice.get("customer")
    store = _store_for_customer(customer_id, table=table)
    if not store:
        return {"handled": False, "reason": "unknown customer"}
    amount = (invoice.get("amount_paid") or 0) / 100
    if amount <= 0:
        return {"handled": False, "reason": "zero-amount invoice"}
    grant(store, amount, kind="subscription_renewal", detail="subscription period",
         stripe_event_id=event_id)
    return {"handled": True, "ownerId": store.owner_id}


def _on_subscription_changed(sub: dict, kind: str, *, table=None) -> dict:
    customer_id = sub.get("customer")
    store = _store_for_customer(customer_id, table=table)
    if not store:
        return {"handled": False, "reason": "unknown customer"}
    status = "canceled" if kind == "customer.subscription.deleted" else sub.get("status")
    store.update(_pk(store), BILLING_SK, {
        "subscriptionStatus": status, "stripeSubscriptionId": sub.get("id"),
    })
    return {"handled": True, "ownerId": store.owner_id}
