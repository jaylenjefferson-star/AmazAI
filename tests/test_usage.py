"""The usage ledger and the budget gate.

The gate is the part worth defending: it runs before a provider is called,
and it has to hold when several runs start at once.
"""

import pytest

from amazai import usage as U
from amazai.usage import Plan, TokenCounts, UsageLedger, UsageStatus


def tokens(i=1000, o=500, **kw):
    return TokenCounts(input=i, output=o, **kw)


def ledger(store, plan=None):
    return UsageLedger(store, plan=plan or U.PLANS["personal"])


def reserve(led, **kw):
    base = dict(user_id="auth0|abc", agent_id="eng", task_id="t-1", run_id="r-1",
                provider="bedrock", model_id="us.anthropic.claude-sonnet-5-v1",
                tokens=tokens())
    base.update(kw)
    return led.reserve(**base)


class TestPricing:
    def test_a_specific_model_beats_its_family(self):
        """Longest prefix wins, so adding an exact entry does not require
        removing the family fallback."""
        t = tokens(1_000_000, 0)
        opus = U.cost_micros("us.anthropic.claude-opus-5-v1", t)
        family = U.cost_micros("us.anthropic.claude-something-new", t)
        assert opus == 15 * U.MICRO
        assert family == 3 * U.MICRO

    def test_an_unknown_provider_still_prices(self):
        """A model we have never seen must not make the ledger throw — an
        estimate that is wrong is recoverable, a crash before the call is
        not."""
        assert U.cost_micros("openai.something", tokens()) > 0

    def test_unreported_token_kinds_stay_absent(self):
        """`None` and `0` mean different things: not reported, versus
        reported as none."""
        item = tokens().to_item()
        assert "cachedTokens" not in item
        assert tokens(cached=0).to_item()["cachedTokens"] == 0


class TestReserveAndFinalize:
    def test_an_estimate_counts_against_the_budget_immediately(self, store):
        """Otherwise ten concurrent runs each see a budget none of them has
        spent yet, and all ten pass a check only one should."""
        led = ledger(store)
        assert led.spent_micros() == 0
        reserve(led)
        assert led.spent_micros() > 0

    def test_finalizing_replaces_the_estimate(self, store):
        led = ledger(store)
        r = reserve(led, tokens=tokens(1000, 500))
        estimated = led.spent_micros()

        led.finalize(r, tokens=tokens(4000, 2000))

        assert led.spent_micros() > estimated
        row = led.month_entries()[0]
        assert row["status"] == UsageStatus.FINALIZED.value
        assert row["outputTokens"] == 2000
        assert row["completedAt"] is not None

    def test_provider_cost_wins_over_the_table(self, store):
        """Our prices are an estimate; theirs is the invoice."""
        led = ledger(store)
        r = reserve(led)
        led.finalize(r, tokens=tokens(), provider_cost_micros=123_456)

        row = led.month_entries()[0]
        assert row["costMicros"] == 123_456
        assert row["costSource"] == "provider"

    def test_a_failed_call_releases_its_reservation(self, store):
        """The row stays — an attempt is a fact — but stops counting."""
        led = ledger(store)
        r = reserve(led)
        assert led.spent_micros() > 0

        led.fail(r, detail="provider timeout")

        assert led.spent_micros() == 0
        assert led.month_entries()[0]["status"] == UsageStatus.FAILED.value

    def test_an_adjustment_records_what_it_changed(self, store):
        led = ledger(store)
        r = reserve(led)
        led.finalize(r, tokens=tokens())
        entry = led.month_entries()[0]

        led.adjust(entry, micros=0, reason="refunded: duplicate run")

        after = led.month_entries()[0]
        assert after["status"] == UsageStatus.ADJUSTED.value
        assert after["costMicros"] == 0
        assert after["adjustment"]["reason"] == "refunded: duplicate run"


class TestTheGate:
    def test_a_run_over_the_per_run_cap_is_refused(self, store):
        led = ledger(store)
        verdict = led.check(model_id="us.anthropic.claude-opus-5-v1",
                            estimated_micros=9 * U.MICRO)
        assert verdict.allowed is False
        assert "per-run cap" in verdict.reason

    def test_the_monthly_ceiling_stops_the_next_run(self, store):
        led = ledger(store, Plan(key="tiny", label="Tiny",
                                 monthly_cost_micros=1 * U.MICRO,
                                 per_run_cost_micros=5 * U.MICRO))
        reserve(led, tokens=tokens(400_000, 0))   # ~$1.20, over the ceiling

        verdict = led.check(model_id="us.anthropic.claude-sonnet-5-v1",
                            estimated_micros=1000)
        assert verdict.allowed is False
        assert "monthly ceiling" in verdict.reason

    def test_a_warning_is_still_an_allowed_run(self, store):
        """A warning that blocked would just be a lower ceiling."""
        led = ledger(store, Plan(key="w", label="W", monthly_cost_micros=10 * U.MICRO,
                                 warn_fraction=0.5))
        reserve(led, tokens=tokens(2_000_000, 0))   # ~$6, past 50% of $10

        verdict = led.check(model_id="us.anthropic.claude-sonnet-5-v1",
                            estimated_micros=1000)
        assert verdict.allowed is True
        assert verdict.warn is True

    def test_a_soft_plan_never_hard_stops(self, store):
        led = ledger(store, U.PLANS["unmetered"])
        reserve(led, tokens=tokens(9_000_000, 0))
        assert led.check(model_id="x", estimated_micros=10 * U.MICRO).allowed is True

    def test_a_model_outside_the_allowlist_is_refused(self, store):
        """An allowlist, never a denylist: a new model has to be allowed
        deliberately rather than by being forgotten."""
        led = ledger(store, Plan(key="a", label="A",
                                 model_allowlist=("claude-haiku",)))
        assert led.check(model_id="us.anthropic.claude-opus-5-v1",
                         estimated_micros=10).allowed is False
        assert led.check(model_id="us.anthropic.claude-haiku-4-5-v1",
                         estimated_micros=10).allowed is True

    def test_a_per_agent_budget_is_separate_from_the_workspace(self, store):
        led = ledger(store, U.PLANS["unmetered"])
        reserve(led, agent_id="eng", tokens=tokens(400_000, 0))

        blocked = led.check(model_id="x", estimated_micros=1000, agent_id="eng",
                            agent_month_limit_micros=1 * U.MICRO)
        other = led.check(model_id="x", estimated_micros=1000, agent_id="ops",
                          agent_month_limit_micros=1 * U.MICRO)

        assert blocked.allowed is False
        assert other.allowed is True


class TestSummary:
    def test_the_summary_splits_by_model_and_agent(self, store):
        led = ledger(store, U.PLANS["unmetered"])
        reserve(led, agent_id="eng", model_id="us.anthropic.claude-opus-5-v1")
        reserve(led, agent_id="ops", model_id="us.anthropic.claude-haiku-4-5-v1")

        s = led.summary()
        assert set(s["byAgent"]) == {"eng", "ops"}
        assert len(s["byModel"]) == 2
        assert s["runs"] == 2

    def test_demo_rows_are_marked_on_the_row(self, store):
        """A global flag would lose the distinction the moment a workspace
        has both kinds."""
        led = ledger(store, U.PLANS["unmetered"])
        reserve(led, demo=True)
        assert led.summary()["demoOnly"] is True

        reserve(led, demo=False)
        assert led.summary()["demoOnly"] is False

    def test_failed_runs_are_excluded_from_the_summary(self, store):
        led = ledger(store, U.PLANS["unmetered"])
        r = reserve(led)
        led.fail(r)
        assert led.summary()["runs"] == 0
