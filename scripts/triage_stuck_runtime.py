#!/usr/bin/env python3
"""Inspect a stuck shared harness and say which generation to adopt.

Live inventory 2026-09-28, us-west-2: `amazai_shared_6c5bb78ac28b_3` is
CREATE_FAILED and generation 2 of that digest is READY. The provisioner
adopts that READY generation on the next ensure (`choose_ready_harness` /
`_adopt_if_actually_ready`). This command is the read-only view of the same
decision. It does not delete a harness and it does not write DynamoDB
unless `--owner-id` and `--apply` are both set, in which case it runs the
normal claim path for that owner — the same path a retry of Meet first Bot
runs after this change.

    python3 scripts/triage_stuck_runtime.py
    python3 scripts/triage_stuck_runtime.py --owner-id 'auth0|...' --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from amazai.standard_runtime import (  # noqa: E402
    STUCK_SHARED_HARNESS,
    choose_ready_harness,
)


def list_matching(prefix: str, region: str) -> list[dict]:
    import boto3
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    records = []
    request: dict = {}
    while True:
        page = control.list_harnesses(**request)
        for row in page.get("harnessSummaries", page.get("harnesses", [])):
            name = row.get("harnessName") or row.get("name") or ""
            if prefix and not name.startswith(prefix):
                continue
            records.append({
                "name": name,
                "status": row.get("status") or "",
                "arn": row.get("harnessArn") or row.get("arn") or "",
            })
        token = page.get("nextToken") or page.get("NextToken")
        if not token:
            return records
        request = {"nextToken": token}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default=STUCK_SHARED_HARNESS.rsplit("_", 1)[0],
                        help="harness-name prefix (default: the live stuck digest)")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"))
    parser.add_argument("--owner-id", default="",
                        help="Auth0 subject whose RUNTIME#standard row should adopt")
    parser.add_argument("--apply", action="store_true",
                        help="with --owner-id, run ensure_shared_harness (adopts READY)")
    args = parser.parse_args()

    print(f"Stuck generation named in inventory: {STUCK_SHARED_HARNESS}")
    try:
        records = list_matching(args.prefix, args.region)
    except Exception as exc:  # noqa: BLE001 -- this is an ops probe, say why it stopped
        print(f"Could not list harnesses ({type(exc).__name__}: {exc})")
        print("Nothing was changed.")
        return 1

    if not records:
        print(f"No harnesses with prefix {args.prefix!r} in {args.region}.")
        return 0

    for row in sorted(records, key=lambda r: r["name"]):
        print(f"  {row['status'] or '?':<16} {row['name']}")
    chosen = choose_ready_harness(records)
    if chosen:
        print(f"Adopt: {chosen['name']} ({chosen['status']})")
    else:
        print("No READY generation to adopt. A retry will rotate past CREATE_FAILED.")

    if args.apply and args.owner_id:
        from amazai.standard_runtime import ensure_shared_harness
        from amazai.store import Store
        arn = ensure_shared_harness(
            Store(args.owner_id),
            wait_seconds=30,
            ready_wait_seconds=240,
            release_on_timeout=True,
        )
        print(f"Runtime row now points at {arn}")
    elif args.apply:
        print("--apply without --owner-id does not write. Pass the Auth0 subject.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
