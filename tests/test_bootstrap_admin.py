"""The admin bootstrap seats a full-capability ACTIVE Owner, idempotently, and
never touches a password.

The seeding logic is factored into bootstrap_admin.seed_owner so it can be
exercised against the moto `store` fixture without argparse or boto3.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from amazai import directory as D

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "bootstrap_admin", ROOT / "scripts" / "bootstrap_admin.py")
bootstrap_admin = importlib.util.module_from_spec(spec)
sys.modules["bootstrap_admin"] = bootstrap_admin
spec.loader.exec_module(bootstrap_admin)


ADMIN = "admin@amazai.co"


def _member_and_audit(rows):
    member = next(r for r in rows if r["entity"] == "Member")
    audit = next(r for r in rows if r["entity"] == "AdminAuditEvent")
    return member, audit


class TestSeedOwner:
    def test_seeds_an_active_owner_membership(self, store):
        rows = bootstrap_admin.seed_owner(store, ADMIN, ADMIN)
        member, _ = _member_and_audit(rows)

        m = D.membership_of(member)
        assert m.role is D.Role.OWNER
        assert m.state is D.MemberState.ACTIVE
        assert m.is_active

    def test_the_seeded_owner_holds_every_capability(self, store):
        """'Full admin' == an Owner holds every capability the matrix names."""
        rows = bootstrap_admin.seed_owner(store, ADMIN, ADMIN)
        member, _ = _member_and_audit(rows)
        m = D.membership_of(member)

        for cap in D.CAPABILITIES:
            assert D.can(m, cap) is True, f"owner should hold {cap.value}"

    def test_writes_an_admin_audit_row(self, store):
        rows = bootstrap_admin.seed_owner(store, ADMIN, ADMIN)
        _, audit = _member_and_audit(rows)

        assert audit["action"] == "member.invited"
        assert audit["detail"] == "bootstrap admin owner seeded"
        assert audit["after"]["role"] == D.Role.OWNER.value

    def test_the_rows_are_actually_persisted(self, store):
        from amazai import keys as K
        bootstrap_admin.seed_owner(store, ADMIN, ADMIN)

        stored = store.try_get(K.org_pk(ADMIN), K.member_sk(ADMIN))
        assert stored is not None
        assert D.membership_of(stored).role is D.Role.OWNER

    def test_re_running_is_idempotent(self, store):
        first = bootstrap_admin.seed_owner(store, ADMIN, ADMIN)
        assert first, "first run should write rows"

        second = bootstrap_admin.seed_owner(store, ADMIN, ADMIN)
        assert second == [], "re-run must not write a duplicate"

        # And exactly one membership row exists.
        from amazai import keys as K
        members = store.query(K.org_pk(ADMIN), sk_prefix="MEMBER#")
        assert len(members) == 1

    def test_no_field_carries_a_password_like_literal(self, store):
        """Belt-and-braces on the credential constraint: the produced rows
        contain no password. We assert no value looks like a secret and that
        no key hints at one."""
        rows = bootstrap_admin.seed_owner(store, ADMIN, ADMIN)

        def walk(value):
            if isinstance(value, dict):
                for k, v in value.items():
                    assert "password" not in k.lower(), f"password-like key {k!r}"
                    assert "secret" not in k.lower(), f"secret-like key {k!r}"
                    yield from walk(v)
            elif isinstance(value, (list, tuple)):
                for v in value:
                    yield from walk(v)
            else:
                yield value

        for row in rows:
            for leaf in walk(row):
                if isinstance(leaf, str):
                    assert "password" not in leaf.lower()


class TestSeedOwnerGuards:
    def test_the_default_admin_email_is_the_documented_one(self):
        assert bootstrap_admin.DEFAULT_ADMIN_EMAIL == ADMIN
