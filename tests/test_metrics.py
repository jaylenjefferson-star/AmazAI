"""CloudWatch metrics via EMF: one JSON line, no `cloudwatch:PutMetricData`,
no IAM change. This is the shape CloudWatch Logs actually requires to parse a
log event into a custom metric on ingest -- these tests are the contract
that shape stays right, not a test of CloudWatch itself.
"""

import json

from amazai import metrics


def _emitted(capsys) -> dict:
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1, f"expected exactly one line, got {len(out)}: {out}"
    return json.loads(out[-1])


class TestEmit:
    def test_a_bare_metric_carries_the_service_dimension(self, capsys):
        metrics.emit("Widgets", 3)
        record = _emitted(capsys)

        assert record["Widgets"] == 3
        assert record["Service"] == "amazai"
        meta = record["_aws"]["CloudWatchMetrics"][0]
        assert meta["Namespace"] == "AmazAI"
        assert meta["Metrics"] == [{"Name": "Widgets", "Unit": "Count"}]
        assert meta["Dimensions"] == [["Service"]]

    def test_extra_dimensions_are_grouped_on_and_printed(self, capsys):
        metrics.emit("RunTimedOut", 1, dimensions={"Reason": "heartbeat_and_deadline"})
        record = _emitted(capsys)

        assert record["Reason"] == "heartbeat_and_deadline"
        meta = record["_aws"]["CloudWatchMetrics"][0]
        assert meta["Dimensions"] == [["Service", "Reason"]]

    def test_properties_are_printed_but_never_counted_as_dimensions(self, capsys):
        """An id belongs here, not in `dimensions` -- CloudWatch bills and
        stores per unique dimension combination, and a runId in that set
        would mean one metric per run forever."""
        metrics.emit("TaskActiveChildren", 2, taskId="run_abc", childRunId="run_def")
        record = _emitted(capsys)

        assert record["taskId"] == "run_abc"
        assert record["childRunId"] == "run_def"
        meta = record["_aws"]["CloudWatchMetrics"][0]
        assert "taskId" not in meta["Dimensions"][0]
        assert "childRunId" not in meta["Dimensions"][0]

    def test_a_custom_unit_is_carried_through(self, capsys):
        metrics.emit("RunQueueDelaySeconds", 4.2, unit="Seconds")
        record = _emitted(capsys)
        assert record["_aws"]["CloudWatchMetrics"][0]["Metrics"][0]["Unit"] == "Seconds"

    def test_non_string_property_values_still_serialize(self, capsys):
        """A property is printed for a Logs Insights query to read, not
        parsed back by this code -- but it still has to be valid JSON."""
        metrics.emit("TaskFanInWake", 1, childCount=3, doneCount=2)
        record = _emitted(capsys)
        assert record["childCount"] == 3
        assert record["doneCount"] == 2
