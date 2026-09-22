"""The identity boundary.

One rule under test above all others: the subject comes from a verified
token, never from the request. A user id in a path, body, header or query
string is a claim by the caller about who they are.
"""

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from amazai import identity as I, keys as K

DOMAIN = "dev-test.us.auth0.com"
AUDIENCE = "https://api.amazai.co"


@pytest.fixture
def signing(monkeypatch):
    """A local RSA key standing in for Auth0's, served as a JWKS."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk["kid"] = "test-key"
    jwk["alg"] = "RS256"
    jwk["use"] = "sig"

    monkeypatch.setenv("AUTH0_DOMAIN", DOMAIN)
    monkeypatch.setenv("AUTH0_AUDIENCE", AUDIENCE)
    monkeypatch.setattr(I, "_fetch_jwks", lambda: {"keys": [jwk]})
    I._jwks, I._jwks_at = None, 0.0
    return key


def token(signing, **over):
    claims = {
        "sub": "auth0|owner-1",
        "iss": f"https://{DOMAIN}/",
        "aud": AUDIENCE,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
        "email": "owner@example.com",
        "email_verified": True,
        "scope": "openid profile email",
    }
    claims.update(over)
    for k in [k for k, v in claims.items() if v is None]:
        del claims[k]
    return jwt.encode(claims, signing, algorithm="RS256",
                      headers={"kid": over.pop("kid", "test-key")})


class TestVerification:
    def test_a_good_token_yields_its_subject(self, signing):
        p = I.verify(token(signing))
        assert p.user_id == "auth0|owner-1"
        assert p.email == "owner@example.com"

    def test_a_token_for_another_audience_is_refused(self, signing):
        """The subtle one: correctly signed by the right tenant, but issued
        for somebody else's API. Accepting it means accepting their tokens."""
        with pytest.raises(I.AuthError):
            I.verify(token(signing, aud="https://api.someone-else.com"))

    def test_a_token_from_another_issuer_is_refused(self, signing):
        with pytest.raises(I.AuthError):
            I.verify(token(signing, iss="https://evil.us.auth0.com/"))

    def test_an_expired_token_is_refused(self, signing):
        """A token that *was* valid is not a valid token."""
        past = int(time.time()) - 7200
        with pytest.raises(I.AuthError):
            I.verify(token(signing, iat=past, exp=past + 60))

    def test_a_token_signed_by_another_key_is_refused(self, signing):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        forged = jwt.encode(
            {"sub": "auth0|attacker", "iss": f"https://{DOMAIN}/", "aud": AUDIENCE,
             "iat": int(time.time()), "exp": int(time.time()) + 3600},
            other, algorithm="RS256", headers={"kid": "test-key"})
        with pytest.raises(I.AuthError):
            I.verify(forged)

    def test_an_unsigned_token_is_refused(self, signing):
        """alg=none is the oldest JWT mistake there is."""
        none_token = jwt.encode({"sub": "auth0|attacker", "aud": AUDIENCE,
                                 "iss": f"https://{DOMAIN}/"},
                                key="", algorithm="none")
        with pytest.raises(I.AuthError):
            I.verify(none_token)

    def test_a_token_without_a_subject_is_refused(self, signing):
        with pytest.raises(I.AuthError):
            I.verify(token(signing, sub=None))

    def test_an_absent_token_is_refused(self, signing):
        with pytest.raises(I.AuthError):
            I.verify("")

    def test_verification_is_refused_when_unconfigured(self, monkeypatch):
        """Better to refuse every request than to accept one unverified."""
        monkeypatch.delenv("AUTH0_DOMAIN", raising=False)
        monkeypatch.delenv("AUTH0_AUDIENCE", raising=False)
        with pytest.raises(I.AuthError):
            I.verify("anything")


class TestTheSubjectComesFromTheToken:
    def test_a_client_supplied_user_id_is_ignored(self, signing):
        """The header, the body and the query string all name someone else.
        None of them is consulted."""
        event = {
            "headers": {"authorization": f"Bearer {token(signing)}",
                        "x-user-id": "auth0|someone-else"},
            "queryStringParameters": {"userId": "auth0|someone-else"},
            "body": json.dumps({"userId": "auth0|someone-else"}),
        }
        assert I.principal_from_event(event).user_id == "auth0|owner-1"

    def test_the_bearer_prefix_is_optional_but_the_token_is_not(self, signing):
        raw = token(signing)
        assert I.bearer({"headers": {"authorization": f"Bearer {raw}"}}) == raw
        assert I.bearer({"headers": {"Authorization": f"bearer {raw}"}}) == raw
        assert I.bearer({"headers": {}}) == ""


class TestUserRecord:
    def test_first_sight_creates_the_record(self, store, signing):
        p = I.verify(token(signing))
        row, created = I.ensure_user(store, p)
        assert created is True
        assert row["userId"] == "auth0|owner-1"
        assert row["provider"] == "auth0"
        assert store.get(K.user_pk(p.user_id), "META")["email"] == "owner@example.com"

    def test_a_returning_user_is_not_duplicated(self, store, signing):
        p = I.verify(token(signing))
        first, created_first = I.ensure_user(store, p)
        second, created_second = I.ensure_user(store, p)
        assert created_first is True
        assert created_second is False
        assert first["createdAt"] == second["createdAt"]
        assert second["lastSeenAt"] >= first["lastSeenAt"]

    def test_a_changed_email_is_refreshed_but_never_keyed_on(self, store, signing):
        I.ensure_user(store, I.verify(token(signing)))
        moved = I.verify(token(signing, email="new@example.com"))
        row, created = I.ensure_user(store, moved)

        assert created is False
        assert row["email"] == "new@example.com"
        # Same subject, so still one record.
        assert row["pk"] == K.user_pk("auth0|owner-1")


class TestOwnerAllowlist:
    def test_no_allowlist_means_open(self, signing, monkeypatch):
        monkeypatch.delenv("OWNER_SUBJECTS", raising=False)
        monkeypatch.delenv("OWNER_EMAILS", raising=False)
        I.assert_owner(I.verify(token(signing)))

    def test_a_listed_subject_passes(self, signing, monkeypatch):
        monkeypatch.setenv("OWNER_SUBJECTS", "auth0|owner-1")
        I.assert_owner(I.verify(token(signing)))

    def test_an_unlisted_subject_is_refused(self, signing, monkeypatch, table):
        # Refusal now falls through to the DB-backed allowlist too (see
        # TestDbAllowlist below); an empty mocked table is what makes "and
        # isn't in the DB list either" true here rather than a real AWS call.
        monkeypatch.setattr(I, "_table", lambda: table)
        monkeypatch.setenv("OWNER_SUBJECTS", "auth0|somebody-else")
        with pytest.raises(I.AuthError):
            I.assert_owner(I.verify(token(signing)))

    def test_an_unverified_email_never_satisfies_the_allowlist(self, signing, monkeypatch, table):
        """Otherwise anyone who can sign up with an address gets in before
        proving they hold it."""
        monkeypatch.setattr(I, "_table", lambda: table)
        monkeypatch.setenv("OWNER_EMAILS", "owner@example.com")
        unverified = I.verify(token(signing, email_verified=False))
        with pytest.raises(I.AuthError):
            I.assert_owner(unverified)


class TestDbAllowlist:
    """The second door: OWNER_SUBJECTS/OWNER_EMAILS still work unchanged
    (an env var, redeploy to add one more person); this is the same gate
    with entries a signed-in owner can add or remove without one."""

    def test_allowing_by_email_lets_a_verified_match_in(self, signing, monkeypatch, table):
        monkeypatch.setattr(I, "_table", lambda: table)
        monkeypatch.setenv("OWNER_SUBJECTS", "auth0|the-operator")
        I.allow("owner@example.com")

        I.assert_owner(I.verify(token(signing)))  # sub=auth0|owner-1, email=owner@example.com

    def test_an_unverified_email_does_not_satisfy_the_db_allowlist_either(
            self, signing, monkeypatch, table):
        monkeypatch.setattr(I, "_table", lambda: table)
        monkeypatch.setenv("OWNER_SUBJECTS", "auth0|the-operator")
        I.allow("owner@example.com")

        unverified = I.verify(token(signing, email_verified=False))
        with pytest.raises(I.AuthError):
            I.assert_owner(unverified)

    def test_allowing_by_subject_lets_that_subject_in(self, signing, monkeypatch, table):
        monkeypatch.setattr(I, "_table", lambda: table)
        monkeypatch.setenv("OWNER_SUBJECTS", "auth0|the-operator")
        I.allow("auth0|owner-1")

        I.assert_owner(I.verify(token(signing, email=None, email_verified=False)))

    def test_disallow_revokes_it(self, signing, monkeypatch, table):
        monkeypatch.setattr(I, "_table", lambda: table)
        monkeypatch.setenv("OWNER_SUBJECTS", "auth0|the-operator")
        I.allow("owner@example.com")
        I.disallow("owner@example.com")

        with pytest.raises(I.AuthError):
            I.assert_owner(I.verify(token(signing)))

    def test_allow_is_case_insensitive_and_idempotent(self, monkeypatch, table):
        monkeypatch.setattr(I, "_table", lambda: table)
        I.allow("Friend@Example.com")
        I.allow("friend@example.com")

        entries = I.list_allowed()
        assert [e["value"] for e in entries] == ["friend@example.com"]

    def test_list_allowed_reports_who_added_each_entry(self, monkeypatch, table):
        monkeypatch.setattr(I, "_table", lambda: table)
        I.allow("friend@example.com", added_by="auth0|the-operator")

        entries = I.list_allowed()
        assert entries[0]["addedBy"] == "auth0|the-operator"

    def test_an_unrelated_email_is_still_refused(self, signing, monkeypatch, table):
        monkeypatch.setattr(I, "_table", lambda: table)
        monkeypatch.setenv("OWNER_SUBJECTS", "auth0|the-operator")
        I.allow("somebody-else@example.com")

        with pytest.raises(I.AuthError):
            I.assert_owner(I.verify(token(signing)))
