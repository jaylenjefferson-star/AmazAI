#!/usr/bin/env python3
"""Check a Composio project key against the live service. Read-only.

    export COMPOSIO_API_KEY=...        # in your own shell; never pasted into chat
    python3 scripts/check_composio.py
    python3 scripts/check_composio.py --user-id <auth0 sub>
    python3 scripts/check_composio.py --user-id <auth0 sub> \\
        --tool <TOOL_SLUG> --arguments '{"...": "..."}'

The key is read from the environment of this process only. The deployed system
reads it from Secrets Manager (`amazai/composio`); this script never touches AWS.
Exit 0 ok, 1 a call failed, 2 refused (the tool was not read-only).
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

from amazai import composio as cp  # noqa: E402
from amazai.composio_check import run_check  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user-id", default="", help="the person whose connections to look at")
    ap.add_argument("--tool", default="", help="one read-only tool to run, by its slug")
    ap.add_argument("--arguments", default="{}", help="the tool's inputs, as JSON")
    args = ap.parse_args()

    key = os.environ.get("COMPOSIO_API_KEY", "").strip()
    if not key:
        print("COMPOSIO_API_KEY is not set in this shell.", file=sys.stderr)
        return 1
    try:
        arguments = json.loads(args.arguments)
    except ValueError:
        print("--arguments must be valid JSON.", file=sys.stderr)
        return 1

    code, lines = run_check(cp.Composio(api_key=key), user_id=args.user_id,
                            tool=args.tool, arguments=arguments)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
