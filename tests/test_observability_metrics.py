"""Metrics actually fire from the real orchestrator loop, not just from
`amazai.metrics.emit` in isolation -- these drive `_drive` for real (only the
harness stream is faked, via `test_drive_loop.World`) and read the metric
back off stdout.
"""

import json

from amazai.states import RunState

from tests.test_agents_api import api_table, call  # noqa: F401
from tests.test_drive_loop import text, tool_use, world  # noqa: F401


def _metrics(capsys, name: str) -> list[dict]:
    out = capsys.readouterr().out.strip().splitlines()
    records = []
    for line in out:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and name in record and "_aws" in record:
            records.append(record)
    return records


class TestQueueDelay:
    def test_a_freshly_queued_run_reports_its_own_queue_delay_once(self, world, capsys):
        world.script([text("done")])

        out = world.drive()

        assert out["state"] == RunState.COMPLETED.value
        emitted = _metrics(capsys, "RunQueueDelaySeconds")
        assert len(emitted) == 1
        assert emitted[0]["RunQueueDelaySeconds"] >= 0
        assert emitted[0]["runId"] == world.run["runId"]

    def test_a_retried_run_does_not_report_a_second_queue_delay(self, world, capsys, monkeypatch):
        """The metric marks *queued -> picked up*, once; a retried
        invocation re-enters `_drive` in RETRYING, past QUEUED, and must not
        look like the run was queued again."""
        import handlers.orchestrator as orch
        monkeypatch.setattr(orch, "_reinvoke", lambda *a, **k: None)

        def unavailable(_):
            raise RuntimeError("service unavailable")

        world.script(unavailable)
        world.drive()
        capsys.readouterr()  # discard the first call's own emissions

        world.script([text("done")])
        out = world.drive({"runId": world.run["runId"], "resume": True})

        assert out["state"] == RunState.COMPLETED.value
        assert _metrics(capsys, "RunQueueDelaySeconds") == []


class TestRetryAttempt:
    def test_a_transient_failure_reports_the_retry_attempt(self, world, capsys, monkeypatch):
        import handlers.orchestrator as orch
        monkeypatch.setattr(orch, "_reinvoke", lambda *a, **k: None)

        def unavailable(_):
            raise RuntimeError("service unavailable")

        world.script(unavailable)
        out = world.drive()

        assert out["state"] == RunState.RETRYING.value
        emitted = _metrics(capsys, "RunRetryAttempt")
        assert len(emitted) == 1
        assert emitted[0]["RunRetryAttempt"] == 1
        assert emitted[0]["ErrorClass"] == "transient"
        assert emitted[0]["runId"] == world.run["runId"]


class TestRunSettled:
    """Before this, a run's own duration/outcome never reached CloudWatch at
    all -- only sweeper-caused terminations did (`RunSweepSealed`). This is
    the metric that answers "how long did this run take, and how" without
    opening its evidence bundle."""

    def test_a_completed_run_reports_its_own_duration_and_outcome(self, world, capsys):
        world.script([text("done")])

        out = world.drive()

        assert out["state"] == RunState.COMPLETED.value
        emitted = _metrics(capsys, "RunSettled")
        assert len(emitted) == 1
        assert emitted[0]["State"] == "COMPLETED"
        assert emitted[0]["RunSettled"] >= 0
        assert emitted[0]["runId"] == world.run["runId"]
        assert emitted[0]["agentId"] == world.run["agentId"]
        assert "toolCallCount" in emitted[0]
        assert "modelCalls" in emitted[0]

    def test_a_terminal_failure_carries_its_error_class(self, world, capsys):
        def bad_request(_):
            raise RuntimeError("must alternate between user and assistant")

        world.script(bad_request)
        out = world.drive()

        assert out["state"] == RunState.FAILED.value
        emitted = _metrics(capsys, "RunSettled")
        assert len(emitted) == 1
        assert emitted[0]["State"] == "FAILED"
        assert emitted[0]["ErrorClass"] == "terminal"


class TestRunContextMessages:
    def test_a_run_reports_how_many_messages_it_sent_the_model(self, world, capsys):
        world.script([text("done")])

        world.drive()

        emitted = _metrics(capsys, "RunContextMessages")
        assert len(emitted) == 1
        assert emitted[0]["RunContextMessages"] >= 1
        assert emitted[0]["runId"] == world.run["runId"]


class TestToolLatency:
    def test_an_inline_tool_call_reports_its_own_latency(self, world, capsys):
        world.script([*tool_use("remember", {"scope": "agent", "body": "the sky is blue"}),
                      text("noted")])

        world.drive()

        emitted = _metrics(capsys, "ToolLatencyMs")
        assert len(emitted) == 1
        assert emitted[0]["Tool"] == "remember"
        assert emitted[0]["ToolLatencyMs"] >= 0
