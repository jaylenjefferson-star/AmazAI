"""CloudWatch metrics via the Embedded Metric Format (EMF).

One structured line to stdout is the entire cost of a metric here: CloudWatch
Logs parses a log event's `_aws` block into a real custom metric on ingest,
so this needs nothing `cloudwatch:PutMetricData` needs -- no IAM permission
beyond what every Lambda already has (it is how `print()` reaches CloudWatch
Logs at all), no client, no throttling to handle, no infra change. Exactly as
available as the `print(json.dumps(...))` diagnostics already scattered
through this codebase, because it is the same call.

https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format_Specification.html
"""

from __future__ import annotations

import json
import time
from datetime import datetime

NAMESPACE = "AmazAI"


def emit(metric: str, value: float, *, unit: str = "Count",
         dimensions: dict[str, str] | None = None, **properties) -> None:
    """Write one EMF log line.

    `dimensions` are both what CloudWatch groups this metric's data points
    by *and* printed on the line; keep these to a handful of fixed, low-
    cardinality values (a bucketed reason, an outcome) -- CloudWatch bills
    and stores per unique dimension combination, so an id in here is not a
    dimension, it is a way to make one. `properties` are printed only, for a
    CloudWatch Logs Insights query to filter or join a metric's own log line
    by -- this is where a runId or taskId belongs, readable but not counted.

    `Service: "amazai"` is always present so every emission carries at least
    one dimension; EMF requires a non-empty dimension set.
    """
    dims = {"Service": "amazai", **(dimensions or {})}
    record = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": NAMESPACE,
                "Dimensions": [list(dims.keys())],
                "Metrics": [{"Name": metric, "Unit": unit}],
            }],
        },
        metric: value,
        **dims,
        **properties,
    }
    print(json.dumps(record, default=str))


def emit_run_settled(run: dict, state_value: str, *, cost_item: dict | None = None,
                     error_class: str = "") -> None:
    """A run's terminal settle, as one metric -- duration, outcome, tool/model
    call counts, retries, tokens. Every number here already existed on the
    run row or the cost ledger; none of it reached CloudWatch before this,
    which meant answering "how long did this take, and how" required opening
    an evidence bundle in S3 for every run, one at a time.

    Shared by `orchestrator._finish`/`_fail` and the sweeper's `_seal` -- both
    are a run's terminal settle, and duplicating this per handler would drift.
    """
    duration = None
    started, ended = run.get("startedAt"), run.get("endedAt")
    if started and ended:
        try:
            duration = (datetime.fromisoformat(ended.replace("Z", "+00:00"))
                       - datetime.fromisoformat(started.replace("Z", "+00:00"))).total_seconds()
        except ValueError:
            duration = None
    dims = {"State": state_value, "Trigger": (run.get("trigger") or {}).get("type", "user")}
    if error_class:
        dims["ErrorClass"] = error_class
    properties = {
        "runId": run.get("runId", ""), "agentId": run.get("agentId", ""),
        "toolCallCount": run.get("toolCallCount", 0), "retryCount": run.get("attempt", 0),
        "iterationCount": (run.get("cursor") or {}).get("turn", 0),
    }
    if cost_item:
        properties.update({"modelCalls": cost_item.get("modelCalls", 0),
                           "inputTokens": cost_item.get("inputTokens", 0),
                           "outputTokens": cost_item.get("outputTokens", 0)})
    emit("RunSettled", duration if duration is not None else 0.0,
        unit="Seconds", dimensions=dims, **properties)
