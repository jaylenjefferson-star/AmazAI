#!/usr/bin/env python3
"""Add, remove, or list who may sign in to this deployment.

identity.assert_owner is the gate every request passes before anything else
runs. It has always had one door: OWNER_SUBJECTS/OWNER_EMAILS, an env var,
which means adding one more person means editing it and redeploying. This is
the second door -- the same gate, but the list lives in DynamoDB, so it can
be managed with this script (or, once signed in, the /allowlist API route)
without a redeploy for every new person.

An email is what you invite someone by, before they have ever signed in; a
Bot only ever checks it against an ID token's *verified* email claim, never
an unverified one. Once someone has signed in at least once, allowing their
Auth0 subject instead is the more precise identity -- an email can be
changed and re-registered elsewhere; a subject cannot.

    python3 scripts/manage_allowlist.py add friend@example.com
    python3 scripts/manage_allowlist.py remove friend@example.com
    python3 scripts/manage_allowlist.py list
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from amazai import identity  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("add", help="allow one more email or Auth0 subject")
    add.add_argument("value")
    add.add_argument("--added-by", default="operator")

    remove = sub.add_parser("remove", help="revoke one email or Auth0 subject")
    remove.add_argument("value")

    sub.add_parser("list", help="show everyone currently allowed")

    args = ap.parse_args()

    if args.cmd == "add":
        entry = identity.allow(args.value, added_by=args.added_by)
        print(f"Allowed: {entry['value']}")
    elif args.cmd == "remove":
        identity.disallow(args.value)
        print(f"Removed: {args.value.strip().lower()}")
    elif args.cmd == "list":
        entries = identity.list_allowed()
        if not entries:
            print("Nobody in the DB-backed allowlist yet.")
            print("(OWNER_SUBJECTS/OWNER_EMAILS, if set, are checked separately.)")
        for e in entries:
            by = f" (added by {e['addedBy']})" if e.get("addedBy") else ""
            print(f"  {e['value']}{by} -- {e.get('addedAt', '?')}")


if __name__ == "__main__":
    main()
