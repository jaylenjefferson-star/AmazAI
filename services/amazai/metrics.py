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
