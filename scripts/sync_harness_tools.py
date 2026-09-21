#!/usr/bin/env python3
"""Give existing harnesses the inline tools added since they were created.

    python3 scripts/sync_harness_tools.py --harness-arn ARN            # read-only report
    python3 scripts/sync_harness_tools.py --harness-arn ARN --apply    # UNVERIFIED update

Inline functions (`request_connector`, `propose_routine`, ...) are declared when a
harness is created. A harness made earlier -- the Engineering seat, say -- does not
have them, so its model is never offered them and no card can come from it.

The orchestrator now does this itself, once per Bot and per set of tools, before a
Bot's first run (`orchestrator._ensure_harness_tools`), so this script is only for
doing it ahead of time or checking what a harness has. A Bot created through the
console is made without them.

The report is safe to run: it only reads. `--apply` calls `update_harness`, whose
parameter shape BUILD_PLAN has not verified; run the report first, and read the
error if it refuses.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from amazai import agentcore  # noqa: E402


def main(argv=None, *, core=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--harness-arn", required=True)
    ap.add_argument("--region", default="us-west-2")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)

    core = core or agentcore.AgentCore(region=args.region)
    report = core.missing_inline_tools(args.harness_arn)
    print(json.dumps(report, indent=2))
    if not args.apply:
        return 0 if report["known"] and not report["missing"] else 1
    if report["known"] and not report["missing"]:
        print("nothing to add")
        return 0
    print(json.dumps(core.add_inline_tools(args.harness_arn), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
