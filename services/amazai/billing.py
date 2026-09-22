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

from amazai import keys as K
from amazai.store import Store, new_id, now_iso
from amazai.usage import MICRO

BILLING_SK = "BILLING"

#: A one-time grant on signup, no clock -- the trial ends when it is spent,
#: not on a timer. See api._warm_account_harness for the sibling signup hook
#: this rides alongside.
TRIAL_GRANT_USD = 5.0

TIER_TRIAL = "trial"


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
        "pk": _pk(store), "sk": f"CREDITLEDGER#{now_iso()}#{new_id()[-6:]}",
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
