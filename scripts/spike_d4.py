#!/usr/bin/env python3
"""Settle decision D4 against a real harness.

    python3 scripts/spike_d4.py --harness-arn ARN --model-id ID            # prints the plan only
    python3 scripts/spike_d4.py --harness-arn ARN --model-id ID --execute  # runs it

What it does: opens two fresh sessions on the harness, makes the model call
`request_approval`, then continues each one -- once with a `toolResult`, once
with a plain user turn -- and reports which the service accepted.

What it costs: four short model calls (a few cents). It creates no agent, writes
nothing to DynamoDB and touches no connector. It needs `bedrock-agentcore:
InvokeHarness` on the harness you name, and nothing else.

Then: set `AMAZAI_CONTINUATION` on the orchestrator Lambda to the value it
prints (`cdk deploy -c continuation=<value>`), and record the result in
docs/architecture/15-open-decisions.md under D4.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from amazai import d4_spike  # noqa: E402


def main(argv=None, *, client=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--harness-arn", required=True)
    ap.add_argument("--model-id", required=True,
                    help="an inference profile id from `aws bedrock list-inference-profiles`; "
                         "never guessed (CLAUDE.md)")
    ap.add_argument("--region", default="us-west-2")
    ap.add_argument("--execute", action="store_true",
                    help="actually call the service (spends a few cents)")
    args = ap.parse_args(argv)

    if not args.execute:
        print("Plan (nothing was called; add --execute to run it):\n"
              f"  harness   {args.harness_arn}\n  model     {args.model_id}\n"
              "  1. open a session; make the model call request_approval\n"
              "  2. continue it with a toolResult          -> tool_result\n"
              "  3. open another; continue it with a user turn -> resume_note\n"
              "  4. report which the service accepted")
        return 0

    if client is None:
        import boto3
        client = boto3.client("bedrock-agentcore", region_name=args.region)

    report = d4_spike.run(client, args.harness_arn, args.model_id)
    print(json.dumps(report, indent=2))
    rec = report["recommendation"]
    if rec == "neither":
        print("\nNeither continuation was accepted. Do not deploy the approval flow; "
              "the errors above are the finding.", file=sys.stderr)
        return 2
    print(f"\nAMAZAI_CONTINUATION={rec}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
