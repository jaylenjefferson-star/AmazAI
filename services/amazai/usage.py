"""Usage ledger: what a run consumed, and whether it was allowed to.

Provider-neutral by construction. Nothing here imports Bedrock, Anthropic or
any other SDK: a run reports tokens and a model identifier, and this module
records them. Adding a provider is adding a row to `PRICING`, not a branch in
the ledger.

Two properties are the whole design:

**Reserve, then finalize.** A run reserves an estimate *before* the provider
is called and adjusts it after. Recording only on completion means a run that
crashes mid-generation consumed tokens nobody is holding, and ten concurrent
runs can each pass a budget check that only one of them should have. The
reservation is what makes the check meaningful.

**The gate is here, not in the UI.** `check` is called by the orchestrator
before a model call. A frontend that hides a button has not enforced
anything; the authoritative refusal happens where the call would be made.

Money is stored in micro-dollars as integers. Floating point cents accumulate
error over thousands of rows, and a ledger that disagrees with itself at the
sixth decimal place is a ledger nobody trusts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from amazai import keys as K
from amazai.store import Store, new_id, now_iso, ordered_suffix

MICRO = 1_000_000


class UsageStatus(str, Enum):
    """Lifecycle of one ledger entry."""
    ESTIMATED = "estimated"    # reserved before the call
    FINALIZED = "finalized"    # provider reported actual usage
    FAILED = "failed"          # the call failed; reservation released
    ADJUSTED = "adjusted"      # corrected after the fact (refund, re-rate)


#: Price per million tokens, in micro-dollars, per provider/model prefix.
#: Matched longest-prefix-first so a family entry covers new point releases
#: without an edit, and an exact entry still wins when one exists.
#:
#: These are list prices for estimation. `finalize` overwrites them with what
#: the provider actually reported whenever the response carries cost, which
#: is why an estimate being slightly wrong is a rounding issue and not a
#: billing one.
PRICING: dict[str, dict[str, int]] = {
    "anthropic.claude-opus-5":    {"input": 15 * MICRO, "output": 75 * MICRO, "cached": 1_500_000},
    "anthropic.claude-sonnet-5":  {"input": 3 * MICRO,  "output": 15 * MICRO, "cached": 300_000},
    "anthropic.claude-haiku-4-5": {"input": 1 * MICRO,  "output": 5 * MICRO,  "cached": 100_000},
    "anthropic.claude":           {"input": 3 * MICRO,  "output": 15 * MICRO, "cached": 300_000},
}

DEFAULT_PRICE = {"input": 3 * MICRO, "output": 15 * MICRO, "cached": 300_000}


def price_for(model_id: str) -> dict[str, int]:
    """Longest matching prefix, so a specific model beats its family."""
    best, best_len = DEFAULT_PRICE, -1
    for prefix, price in PRICING.items():
        if prefix in (model_id or "") and len(prefix) > best_len:
            best, best_len = price, len(prefix)
    return best


@dataclass
class TokenCounts:
    """What a model call consumed.

    `cached` and `reasoning` are optional because not every provider reports
    them. They are counted into `total` when present and simply absent
    otherwise — never zero-filled, which would make "not reported" and
    "reported as none" indistinguishable.
    """
    input: int = 0
    output: int = 0
    cached: int | None = None
    reasoning: int | None = None

    @property
    def total(self) -> int:
        return (self.input + self.output
                + (self.cached or 0) + (self.reasoning or 0))

    def to_item(self) -> dict:
        item = {"inputTokens": self.input, "outputTokens": self.output,
                "totalTokens": self.total}
        if self.cached is not None:
            item["cachedTokens"] = self.cached
        if self.reasoning is not None:
            item["reasoningTokens"] = self.reasoning
        return item


def cost_micros(model_id: str, tokens: TokenCounts) -> int:
    price = price_for(model_id)
    micros = (tokens.input * price["input"] + tokens.output * price["output"]
              + (tokens.cached or 0) * price["cached"])
    return micros // 1_000_000


# --- plans ------------------------------------------------------------------

@dataclass(frozen=True)
class Plan:
    """What a workspace is allowed to spend.

    Shaped for a product that does not exist yet — seats, model allowlists,
    monthly allowances — so that adding billing later is wiring a plan record
    to a payment provider rather than retrofitting limits into a system that
    never had them. No payment code lives here and none should.
    """
    key: str
    label: str
    monthly_token_allowance: int | None = None
    monthly_cost_micros: int | None = None
    per_run_cost_micros: int | None = None
    warn_fraction: float = 0.8
    hard_stop: bool = True
    #: Empty means every model the account offers. Never a denylist: a new
    #: model must be allowed deliberately, not by having been forgotten.
    model_allowlist: tuple[str, ...] = ()
    seats: int = 1


PLANS: dict[str, Plan] = {
    "personal": Plan(
        key="personal", label="Personal",
        monthly_cost_micros=200 * MICRO,
        per_run_cost_micros=5 * MICRO,
        warn_fraction=0.8, hard_stop=True,
    ),
    "unmetered": Plan(
        key="unmetered", label="Unmetered (private testing)",
        monthly_cost_micros=None, per_run_cost_micros=None, hard_stop=False,
    ),
}

DEFAULT_PLAN = "personal"


class BudgetExceeded(RuntimeError):
    """The gate said no. Raised before a provider is called, never after."""


@dataclass
class Verdict:
    allowed: bool
    reason: str = ""
    warn: bool = False
    spent_micros: int = 0
    limit_micros: int | None = None

    @property
    def fraction(self) -> float:
        if not self.limit_micros:
            return 0.0
        return self.spent_micros / self.limit_micros


# --- the ledger -------------------------------------------------------------

@dataclass
class Reservation:
    entry_id: str
    run_id: str
    estimated_micros: int
    item: dict = field(default_factory=dict)


class UsageLedger:
    """Append-only usage, plus the budget gate that reads it."""

    def __init__(self, store: Store, *, plan: Plan | None = None) -> None:
        self.store = store
        self.plan = plan or PLANS[DEFAULT_PLAN]

    # -- reading ------------------------------------------------------------

    def month_entries(self, month: str | None = None,
                      *, agent_id: str | None = None, limit: int = 500) -> list[dict]:
        month = month or now_iso()[:7]
        rows = self.store.query(K.usage_pk(month), limit=limit)
        if agent_id:
            rows = [r for r in rows if r.get("agentId") == agent_id]
        return rows

    def spent_micros(self, month: str | None = None, *, agent_id: str | None = None) -> int:
        """What counts against the budget.

        Estimated entries count. A reservation that is not counted is a
        reservation that does nothing, and concurrent runs would each see a
        budget that none of them had spent yet.
        """
        total = 0
        for row in self.month_entries(month, agent_id=agent_id):
            if row.get("status") == UsageStatus.FAILED.value:
                continue
            total += int(row.get("costMicros", 0))
        return total

    # -- the gate -----------------------------------------------------------

    def check(self, *, model_id: str, estimated_micros: int,
              agent_id: str | None = None,
              agent_month_limit_micros: int | None = None) -> Verdict:
        """Decide whether a model call may proceed.

        Called by the orchestrator before the provider. Returns a verdict
        rather than raising, because a warning is also an answer and the
        caller decides what to do with one.
        """
        if self.plan.model_allowlist and not any(
                m in model_id for m in self.plan.model_allowlist):
            return Verdict(False, f"{model_id} is not in the {self.plan.label} plan")

        if (self.plan.per_run_cost_micros is not None
                and estimated_micros > self.plan.per_run_cost_micros):
            return Verdict(
                False,
                f"estimated ${estimated_micros / MICRO:.2f} exceeds the "
                f"${self.plan.per_run_cost_micros / MICRO:.2f} per-run cap")

        if agent_month_limit_micros is not None:
            agent_spent = self.spent_micros(agent_id=agent_id)
            if agent_spent + estimated_micros > agent_month_limit_micros:
                return Verdict(False, f"agent {agent_id} is at its monthly budget",
                               spent_micros=agent_spent,
                               limit_micros=agent_month_limit_micros)

        limit = self.plan.monthly_cost_micros
        if limit is None:
            return Verdict(True, "no monthly ceiling on this plan")

        spent = self.spent_micros()
        projected = spent + estimated_micros
        if projected > limit and self.plan.hard_stop:
            return Verdict(False, "workspace is at its monthly ceiling",
                           spent_micros=spent, limit_micros=limit)

        return Verdict(True, "", warn=projected > limit * self.plan.warn_fraction,
                       spent_micros=spent, limit_micros=limit)

    # -- writing ------------------------------------------------------------

    def reserve(self, *, user_id: str, agent_id: str, task_id: str, run_id: str,
                provider: str, model_id: str, tokens: TokenCounts,
                source: dict | None = None, demo: bool = False) -> Reservation:
        """Record an estimate before the provider is called."""
        month = now_iso()[:7]
        entry_id = new_id("use_")
        micros = cost_micros(model_id, tokens)

        item = {
            "pk": K.usage_pk(month),
            "sk": f"USE#{now_iso()}#{ordered_suffix()}",
            "entity": "UsageEntry", "entryId": entry_id,
            "gsi1pk": "USAGE", "gsi1sk": f"{now_iso()}#{agent_id}",
            "userId": user_id, "agentId": agent_id,
            "taskId": task_id, "runId": run_id,
            "provider": provider, "model": model_id,
            "requestedAt": now_iso(), "completedAt": None,
            "status": UsageStatus.ESTIMATED.value,
            "costMicros": micros, "estimatedMicros": micros, "actualMicros": None,
            # Demo rows are marked at the row level, not inferred from a
            # global flag, so a workspace that has both can still tell them
            # apart after the fact.
            "demo": bool(demo),
            "source": source or {},
            **tokens.to_item(),
        }
        self.store.put(item)
        return Reservation(entry_id=entry_id, run_id=run_id,
                           estimated_micros=micros, item=item)

    def finalize(self, reservation: Reservation, *, tokens: TokenCounts,
                 provider_cost_micros: int | None = None,
                 source: dict | None = None) -> dict:
        """Replace the estimate with what actually happened.

        `provider_cost_micros` wins when the provider reports cost: our
        price table is an estimate and theirs is the invoice.
        """
        model_id = reservation.item["model"]
        micros = (provider_cost_micros if provider_cost_micros is not None
                  else cost_micros(model_id, tokens))

        changes = {
            "status": UsageStatus.FINALIZED.value,
            "completedAt": now_iso(),
            "costMicros": micros,
            "actualMicros": micros,
            "costSource": "provider" if provider_cost_micros is not None else "table",
            **tokens.to_item(),
        }
        if source:
            changes["source"] = {**reservation.item.get("source", {}), **source}
        return self.store.update(reservation.item["pk"], reservation.item["sk"], changes)

    def fail(self, reservation: Reservation, *, detail: str = "") -> dict:
        """Release a reservation whose call never produced output.

        The row stays — an attempt is a fact — but it stops counting against
        the budget.
        """
        return self.store.update(reservation.item["pk"], reservation.item["sk"], {
            "status": UsageStatus.FAILED.value,
            "completedAt": now_iso(),
            "costMicros": 0,
            "failureDetail": detail[:500],
        })

    def adjust(self, entry: dict, *, micros: int, reason: str) -> dict:
        """Correct a finalized entry. Never rewrites history silently."""
        return self.store.update(entry["pk"], entry["sk"], {
            "status": UsageStatus.ADJUSTED.value,
            "costMicros": micros,
            "adjustment": {"from": entry.get("costMicros"), "to": micros,
                           "reason": reason, "at": now_iso()},
        })

    # -- reporting ----------------------------------------------------------

    def summary(self, month: str | None = None) -> dict:
        """What the Usage screen renders."""
        month = month or now_iso()[:7]
        rows = self.month_entries(month)
        live = [r for r in rows if r.get("status") != UsageStatus.FAILED.value]

        by_model: dict[str, dict] = {}
        by_agent: dict[str, dict] = {}
        for r in live:
            for bucket, key in ((by_model, r.get("model", "unknown")),
                                (by_agent, r.get("agentId", "unknown"))):
                slot = bucket.setdefault(key, {"costMicros": 0, "tokens": 0, "runs": 0})
                slot["costMicros"] += int(r.get("costMicros", 0))
                slot["tokens"] += int(r.get("totalTokens", 0))
                slot["runs"] += 1

        spent = sum(int(r.get("costMicros", 0)) for r in live)
        limit = self.plan.monthly_cost_micros

        return {
            "month": month,
            "plan": {"key": self.plan.key, "label": self.plan.label,
                     "monthlyCostMicros": limit,
                     "perRunCostMicros": self.plan.per_run_cost_micros,
                     "warnFraction": self.plan.warn_fraction,
                     "hardStop": self.plan.hard_stop},
            "spentMicros": spent,
            "totalTokens": sum(int(r.get("totalTokens", 0)) for r in live),
            "runs": len(live),
            "byModel": by_model,
            "byAgent": by_agent,
            "demoOnly": bool(live) and all(r.get("demo") for r in live),
            "recent": sorted(live, key=lambda r: r.get("requestedAt", ""), reverse=True)[:25],
        }
