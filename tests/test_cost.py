import pytest

from amazai.cost import Budget, RunCost, Verdict, check


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
