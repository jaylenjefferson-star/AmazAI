import pytest

from amazai import keys as K
from amazai.cost import Budget, RunCost, Verdict, check, spent_this_month


def _budget(**kw):
    base = dict(per_run_usd=2.0, per_month_usd=40.0)
    base.update(kw)
    return Budget(**base)


class TestRunCost:
    def test_three_way_split_sums_to_total(self):
        c = RunCost()
        c.add_model(0.31, input_tokens=48210, output_tokens=3120)
        c.add_runtime(0.08, seconds=194)
        c.add_connector("github", 0.03, calls=6)
        assert c.total_usd == pytest.approx(0.42)

    def test_connector_calls_accumulate_per_provider(self):
        c = RunCost()
        c.add_connector("github", calls=2)
        c.add_connector("github", calls=4)
        c.add_connector("gmail", calls=1)
        assert c.connector_calls == {"github": 6, "gmail": 1}

    def test_item_carries_all_three_sources(self):
        c = RunCost()
        c.add_model(0.10, input_tokens=100, output_tokens=10)
        item = c.to_item()
        for field in ("modelUsd", "runtimeUsd", "connectorUsd", "totalUsd"):
            assert field in item

    def test_zero_cost_run_is_representable(self):
        assert RunCost().total_usd == 0.0


class TestBudgetVerdicts:
    def test_within_budget(self):
        r = check(_budget(), spent_this_run=0.10, spent_this_month=1.0)
        assert r.verdict is Verdict.OK
        assert not r.should_stop

    def test_warns_at_eighty_percent_of_run_budget(self):
        r = check(_budget(), spent_this_run=1.60, spent_this_month=1.0)
        assert r.verdict is Verdict.WARN

    def test_warns_at_eighty_percent_of_month_budget(self):
        r = check(_budget(), spent_this_run=0.01, spent_this_month=32.0)
        assert r.verdict is Verdict.WARN

    def test_hard_stop_at_run_ceiling(self):
        r = check(_budget(), spent_this_run=2.0, spent_this_month=1.0)
        assert r.verdict is Verdict.STOP
        assert r.should_stop

    def test_hard_stop_at_month_ceiling(self):
        r = check(_budget(), spent_this_run=0.01, spent_this_month=40.0)
        assert r.should_stop

    def test_warn_mode_does_not_stop_on_money(self):
        # D7: routines hard-stop; interactive runs may prefer warn-and-continue.
        r = check(_budget(on_ceiling="warn"), spent_this_run=5.0, spent_this_month=1.0)
        assert r.verdict is Verdict.WARN
        assert not r.should_stop

    def test_fractions_are_reported(self):
        r = check(_budget(), spent_this_run=1.0, spent_this_month=20.0)
        assert r.run_fraction == pytest.approx(0.5)
        assert r.month_fraction == pytest.approx(0.5)


class TestLoopCeilings:
    def test_tool_call_ceiling_stops_the_run(self):
        r = check(_budget(), spent_this_run=0.0, spent_this_month=0.0, tool_calls=60)
        assert r.should_stop
        assert "tool call" in r.reason

    def test_tool_error_ceiling_stops_the_run(self):
        r = check(_budget(), spent_this_run=0.0, spent_this_month=0.0, tool_errors=6)
        assert r.should_stop

    def test_consecutive_error_ceiling_stops_the_run(self):
        r = check(_budget(), spent_this_run=0.0, spent_this_month=0.0,
                  consecutive_tool_errors=3)
        assert r.should_stop

    def test_loop_ceilings_are_hard_even_in_warn_mode(self):
        # Warn mode is about money. "Warn and keep looping" defeats the point
        # of a loop ceiling, which exists to stop the loop.
        r = check(_budget(on_ceiling="warn"), spent_this_run=0.0,
                  spent_this_month=0.0, tool_calls=60)
        assert r.should_stop

    def test_loop_ceiling_takes_precedence_over_money(self):
        r = check(_budget(), spent_this_run=10.0, spent_this_month=0.0, tool_calls=60)
        assert "tool call" in r.reason


class TestDegenerateBudgets:
    def test_zero_budget_does_not_divide_by_zero(self):
        r = check(Budget(per_run_usd=0.0, per_month_usd=0.0),
                  spent_this_run=1.0, spent_this_month=1.0)
        assert r.verdict is Verdict.OK



class TestModelPricing:
    """`model_usd` is the seam between this ledger and the one price table.

    These assert the seam, not the prices: the numbers come from
    `usage.PRICING`, and a price change should edit that table and this file's
    expectations together rather than introduce a second table here.
    """

    def test_a_priced_call_costs_something(self):
        from amazai.cost import model_usd
        assert model_usd("test-model", input_tokens=1000, output_tokens=500) > 0

    def test_default_price_matches_the_usage_table(self):
        from amazai.cost import model_usd
        from amazai.usage import MICRO, TokenCounts, cost_micros
        usd = model_usd("test-model", input_tokens=1000, output_tokens=500)
        expected = cost_micros("test-model", TokenCounts(input=1000, output=500)) / MICRO
        assert usd == pytest.approx(expected)

    def test_a_named_model_beats_its_family(self):
        from amazai.cost import model_usd
        opus = model_usd("anthropic.claude-opus-5-v1", input_tokens=1_000_000)
        family = model_usd("anthropic.claude-something-new", input_tokens=1_000_000)
        assert opus > family

    def test_output_tokens_cost_more_than_input(self):
        from amazai.cost import model_usd
        assert (model_usd("test-model", output_tokens=10_000)
                > model_usd("test-model", input_tokens=10_000))

    def test_cached_tokens_are_cheaper_than_fresh_input(self):
        from amazai.cost import model_usd
        assert (model_usd("test-model", cached_tokens=1_000_000)
                < model_usd("test-model", input_tokens=1_000_000))

    def test_unreported_cached_tokens_do_not_raise(self):
        from amazai.cost import model_usd
        assert model_usd("test-model", input_tokens=10, cached_tokens=None) >= 0

    def test_a_call_with_no_tokens_is_free(self):
        from amazai.cost import model_usd
        assert model_usd("test-model") == 0.0


class TestRunCostAccumulation:
    def test_model_calls_are_counted(self):
        c = RunCost()
        c.add_model(0.01, input_tokens=10, output_tokens=2)
        c.add_model(0.02, input_tokens=20, output_tokens=4)
        assert c.model_calls == 2
        assert c.input_tokens == 30 and c.output_tokens == 6

    def test_cached_tokens_accumulate_and_are_reported(self):
        c = RunCost()
        c.add_model(0.01, input_tokens=10, cached_tokens=500)
        assert c.to_item()["cachedTokens"] == 500

    def test_runtime_seconds_are_recorded_even_when_unpriced(self):
        # Seconds are measured; a harness second has no verified price, so it
        # is rated at zero rather than at a guess.
        c = RunCost()
        c.add_runtime(0.0, seconds=42.5)
        assert c.to_item()["runtimeSeconds"] == 42.5
        assert c.to_item()["runtimeUsd"] == 0.0


class TestTwoOwnersWithTheSameAgentId:
    """`agent_id` is a deterministic slug (e.g. 'chief' -- the default first-
    Bot name), not a `new_id()`-random one, so without `owner_id` in the cost
    row's own pk two owners' identically-named Bot would share one month's
    ledger: same partition, each owner's spend counted against the other's
    budget. See keys.cost_pk."""

    def test_one_owners_spend_never_counts_against_the_other(self, two_stores):
        from amazai.store import now_iso
        store_a, store_b = two_stores
        month = now_iso()[:7]  # spent_this_month always reads the current month
        store_a.put({"pk": K.cost_pk(store_a.owner_id, "chief", month),
                    "sk": K.run_pk("run-a"), "entity": "Cost", "totalUsd": 5.0})
        store_b.put({"pk": K.cost_pk(store_b.owner_id, "chief", month),
                    "sk": K.run_pk("run-b"), "entity": "Cost", "totalUsd": 0.25})

        assert spent_this_month(store_a, "chief") == 5.0
        assert spent_this_month(store_b, "chief") == 0.25
