#!/usr/bin/env python3
"""Seat admin@amazai.co as a full-capability ACTIVE Owner in the Directory.

An Owner already holds every capability in directory.CAPABILITIES, so "full
admin" is not a special role -- it is an ACTIVE Owner MEMBER# row plus the
env-driven owner allowlist that identity.assert_owner and the web isOwner()
already read. This script writes the row; the allowlist is configuration (see
docs/ADMIN_BOOTSTRAP.md and .env.example / web/.env.example).

SAFE BOOTSTRAP PROCEDURE (no password is ever stored here or anywhere in the
repo):

  1. Create the user admin@amazai.co in Auth0. The human sets the password
     THERE, at first login. This script reads no password and writes no
     password; the credential lives only in Auth0.
  2. Set the owner allowlist at deploy time (never a password):
         OWNER_EMAILS=admin@amazai.co
         OWNER_SUBJECTS=<auth0-sub>   # once the sub is known
     and for the console build, VITE_OWNER_EMAIL=admin@amazai.co.
  3. Run this script to seat the ACTIVE Owner row:
         OWNER_ID=<caller-sub> python3 scripts/bootstrap_admin.py \\
             --subject <auth0-sub-of-admin>
     Re-running is safe: if the row already exists the script reports it and
     exits 0 without writing a duplicate.

SINGLE-TENANT SEAM: today store.Store stamps ownerId == the caller and orgId ==
the subject (docs/architecture/03-data-model.md), so this bootstrap seats
admin@amazai.co as the Owner of its OWN org. A true cross-account admin over
OTHER owners' orgs is the deferred multi-tenant follow-up (recorded in the
task's deferred list); it is deliberately NOT built here.

The Auth0 `sub` for admin@amazai.co is not known at build time, so the primary
allowlist path is the email (OWNER_EMAILS). Pass --subject once the sub is
known; until then the row keys on the email as a documented placeholder subject
and the sub is filled by a re-run.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from amazai import directory as D  # noqa: E402
from amazai import govern  # noqa: E402
from amazai import keys as K  # noqa: E402
from amazai.agents import Actor  # noqa: E402
from amazai.store import Store  # noqa: E402

DEFAULT_ADMIN_EMAIL = "admin@amazai.co"


def seed_owner(store: Store, org_id: str, subject: str) -> list[dict]:
    """Seat `subject` as an ACTIVE Owner of `org_id`, idempotently.

    Returns the rows that were written: the ACTIVE Owner MEMBER# row and the
    append-only admin-audit row that records the bootstrap. If the membership
    row already exists this returns an empty list and writes nothing -- the
    re-run is a report, not a second write.

    Row-building is factored out here (rather than living inside main()) so the
    behaviour is testable against the moto store without argparse or boto3. It
    reads no password and writes no password; the credential lives only in
    Auth0.
    """
    if store.try_get(K.org_pk(org_id), K.member_sk(subject)):
        return []

    member = D.member_row(org_id, subject, D.Role.OWNER,
                          state=D.MemberState.ACTIVE)
    # The bootstrap is a governance decision, so it earns an append-only admin
    # audit row alongside the membership. `member.invited` is the closest
    # ADMIN_AUDITED_ACTIONS verb for "an Owner seat came into being".
    actor = Actor(user_id=subject, org_id=org_id)
    audit = govern.admin_audit_event(
        org_id, "member.invited", actor,
        after={"subject": subject, "role": D.Role.OWNER.value,
               "state": D.MemberState.ACTIVE.value},
        detail="bootstrap admin owner seeded",
    )
    return store.transact_put([member, audit])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--email", default=DEFAULT_ADMIN_EMAIL,
                    help="the admin's email (owner allowlist key; default "
                         f"{DEFAULT_ADMIN_EMAIL})")
    ap.add_argument("--subject", default=None,
                    help="the admin's Auth0 `sub`, once known. Until then the "
                         "email is used as the (documented) placeholder subject "
                         "and the real sub is filled by a re-run.")
    ap.add_argument("--org-id", default=None,
                    help="the org to seat the Owner in. Defaults to the "
                         "subject (single-tenant seam: one org per owner).")
    ap.add_argument("--owner", default=os.environ.get("OWNER_ID"),
                    help="the caller identity that stamps ownerId on the rows "
                         "(the Auth0 `sub` of the account owner). Defaults to "
                         "$OWNER_ID.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # No --subject yet is the expected case at first bootstrap: the sub is
    # unknown until the human signs in once, so we key on the email and let a
    # later re-run supply the real sub. This mirrors the OWNER_EMAILS-first
    # allowlist path in identity.assert_owner.
    subject = args.subject or args.email
    org_id = args.org_id or subject

    owner = args.owner or subject
    if not owner:
        print("Provide the caller identity via --owner or $OWNER_ID (the Auth0 "
              "`sub` that owns this deployment).")
        return 1

    if args.dry_run:
        print(f"would seat {subject!r} as ACTIVE Owner of org {org_id!r} "
              f"(email {args.email})")
        print("would write: 1 MEMBER# row + 1 admin-audit row")
        print("no password is read or written")
        return 0

    store = Store(owner)
    rows = seed_owner(store, org_id, subject)
    if not rows:
        print(f"Owner already seated for {subject!r} in org {org_id!r}. "
              "Nothing to do.")
        return 0

    print(f"Seated {subject!r} as ACTIVE Owner of org {org_id!r}.")
    print(f"Wrote {len(rows)} row(s): the Owner membership + a bootstrap "
          "admin-audit row.")
    print("Reminder: set OWNER_EMAILS / OWNER_SUBJECTS (and VITE_OWNER_EMAIL "
          "for the console) so the allowlist recognises this identity. The "
          "password is set by the human in Auth0, never here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
