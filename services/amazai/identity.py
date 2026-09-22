"""Who is calling, established from an Auth0 access token.

The identity boundary. Every authenticated route resolves the caller here and
nowhere else, and the rule that makes it worth having is a single line:

    the subject comes from the verified token, never from the request.

A user id in a path, a body, a header or a query string is a claim by the
caller about who they are. This module never reads one. `Principal.user_id`
is `sub` out of a signature-checked token or the request does not proceed.

Verification checks signature, issuer, audience and expiry — all four. Three
of them are the common mistakes: a token signed by the right tenant for a
*different* audience is a valid token for somebody else's API, and an expired
one is a token that was valid, which is not the same thing.

Cognito is gone from this path rather than kept alongside. Two identity
systems that both "work" is how a request ends up authenticated by whichever
one happened to be checked first.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass

from amazai import keys as K
from amazai.store import Store, now_iso

#: How long a fetched JWKS is trusted. Auth0 rotates signing keys; a cache
#: that never expires survives exactly until the first rotation.
JWKS_TTL_SECONDS = 3600

#: Clock skew allowance. Without it a correctly-issued token can fail `iat`
#: on a machine whose clock is a second fast.
LEEWAY_SECONDS = 60


class AuthError(PermissionError):
    """The token is missing, malformed, or does not verify. Surfaces as 401."""


def domain() -> str:
    return os.environ.get("AUTH0_DOMAIN", "").strip().rstrip("/")


def audience() -> str:
    return os.environ.get("AUTH0_AUDIENCE", "").strip()


def issuer() -> str:
    return f"https://{domain()}/"


def configured() -> bool:
    return bool(domain() and audience())


_jwks: dict | None = None
_jwks_at: float = 0.0


def _fetch_jwks() -> dict:
    global _jwks, _jwks_at
    if _jwks and time.time() - _jwks_at < JWKS_TTL_SECONDS:
        return _jwks
    url = f"https://{domain()}/.well-known/jwks.json"
    with urllib.request.urlopen(url, timeout=5) as resp:
        _jwks = json.loads(resp.read().decode())
    _jwks_at = time.time()
    return _jwks


def _signing_key(token: str):
    """The JWK matching the token's `kid`, as a verification key."""
    import jwt
    from jwt import PyJWKClient  # noqa: F401  (imported for its exceptions)

    try:
        header = jwt.get_unverified_header(token)
    except Exception as exc:  # noqa: BLE001
        raise AuthError("malformed token header") from exc

    kid = header.get("kid")
    if not kid:
        raise AuthError("token has no key id")

    for key in _fetch_jwks().get("keys", []):
        if key.get("kid") == kid:
            return jwt.PyJWK(key).key

    # One retry with a fresh JWKS: a rotation between our cache and this
    # token is the expected reason a kid is unknown.
    global _jwks_at
    _jwks_at = 0.0
    for key in _fetch_jwks().get("keys", []):
        if key.get("kid") == kid:
            return jwt.PyJWK(key).key

    raise AuthError("token signed by an unknown key")


@dataclass(frozen=True)
class Principal:
    """A verified caller.

    `user_id` is the Auth0 `sub`. It is the only identifier any downstream
    code should key on: an email can be changed and re-registered, a `sub`
    cannot.
    """
    user_id: str
    email: str | None = None
    email_verified: bool = False
    scopes: tuple[str, ...] = ()
    raw: dict | None = None

    @property
    def org_id(self) -> str:
        """One workspace per owner today. A seam, not a promise."""
        return self.user_id


def verify(token: str) -> Principal:
    """Verify an Auth0 access token and return the caller it names."""
    import jwt

    if not configured():
        raise AuthError("AUTH0_DOMAIN and AUTH0_AUDIENCE are not configured")
    if not token:
        raise AuthError("no bearer token")

    try:
        claims = jwt.decode(
            token,
            _signing_key(token),
            algorithms=["RS256"],
            audience=audience(),
            issuer=issuer(),
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except AuthError:
        raise
    except Exception as exc:  # noqa: BLE001
        # The reason is logged, not returned: "audience mismatch" tells an
        # attacker which of their guesses was closest.
        print(f"token rejected: {type(exc).__name__}: {exc}")
        raise AuthError("token failed verification") from exc

    sub = claims.get("sub")
    if not sub:
        raise AuthError("token has no subject")

    return Principal(
        user_id=sub,
        email=claims.get("email"),
        email_verified=bool(claims.get("email_verified")),
        scopes=tuple((claims.get("scope") or "").split()),
        raw=claims,
    )


def bearer(event: dict) -> str:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    value = headers.get("authorization", "")
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return value.strip()


def principal_from_event(event: dict) -> Principal:
    """The one way a handler learns who is calling."""
    return verify(bearer(event))


# --- the internal user record -----------------------------------------------

def ensure_user(store: Store, principal: Principal) -> dict:
    """Map an Auth0 subject to an internal user row, creating it on first sight.

    The row exists so everything else can key on a stable internal id and so
    a first sign-in is an event with a timestamp rather than a silent
    side-effect of the first API call.

    Email is stored as a convenience and refreshed on each call, because it
    can change at the identity provider. Nothing keys on it.
    """
    pk = K.user_pk(principal.user_id)
    existing = store.try_get(pk, "META")

    if existing:
        changes = {"lastSeenAt": now_iso()}
        if principal.email and existing.get("email") != principal.email:
            changes["email"] = principal.email
            changes["emailVerified"] = principal.email_verified
        return store.update(pk, "META", changes)

    return store.put({
        "pk": pk, "sk": "META",
        "entity": "User", "userId": principal.user_id,
        "gsi1pk": "USERS", "gsi1sk": now_iso(),
        "provider": "auth0",
        "email": principal.email,
        "emailVerified": principal.email_verified,
        "createdAt": now_iso(), "lastSeenAt": now_iso(),
    })


# --- the owner allowlist ----------------------------------------------------

def owner_subjects() -> tuple[str, ...]:
    raw = os.environ.get("OWNER_SUBJECTS", "")
    return tuple(s.strip() for s in raw.split(",") if s.strip())


def owner_emails() -> tuple[str, ...]:
    raw = os.environ.get("OWNER_EMAILS", "")
    return tuple(s.strip().lower() for s in raw.split(",") if s.strip())


# --- the DB-backed allowlist --------------------------------------------
#
# OWNER_SUBJECTS/OWNER_EMAILS are an env var, which means adding one more
# person means editing it and redeploying -- fine for the single operator
# this started as, not for "invite a friend". This is the same gate, with
# entries a signed-in owner can add or remove through the API instead.
#
# Stored outside any owner's own partition, in one fixed, reserved pk:
# *whether someone may become a tenant at all* has to be readable before this
# request has an owner to scope a Store to -- the one place `ownerId`
# filtering does not apply, same reasoning as `store.discover_owner_ids`.

ALLOWLIST_PK = "PLATFORM#ALLOWLIST"


def _table():
    import boto3
    from amazai.store import TABLE_NAME
    return boto3.resource("dynamodb").Table(TABLE_NAME)


def _allowlist_key(value: str) -> str:
    return value.strip().lower()


def allow(value: str, *, added_by: str = "") -> dict:
    """Let one more person in, by email (what an operator types to invite
    someone who has never signed in) or by Auth0 subject (once they have).
    Idempotent: allowing an already-allowed value just refreshes it."""
    key = _allowlist_key(value)
    if not key:
        raise ValueError("value is required")
    item = {
        "pk": ALLOWLIST_PK, "sk": f"ENTRY#{key}",
        "entity": "AllowlistEntry", "value": key,
        "addedBy": added_by, "addedAt": now_iso(),
    }
    _table().put_item(Item=item)
    return item


def disallow(value: str) -> None:
    _table().delete_item(Key={"pk": ALLOWLIST_PK, "sk": f"ENTRY#{_allowlist_key(value)}"})


def list_allowed() -> list[dict]:
    from boto3.dynamodb.conditions import Key
    resp = _table().query(KeyConditionExpression=Key("pk").eq(ALLOWLIST_PK))
    return sorted(resp.get("Items", []), key=lambda i: i.get("addedAt", ""))


def _is_allowed_in_table(principal: Principal) -> bool:
    """A DynamoDB outage here must read as "not on the list", not as an
    unhandled exception -- this is a security gate, and the failure mode a
    gate must never have is opening because the lock jammed. `assert_owner`
    already has another door (the env allowlist) that does not depend on
    this table at all, so a real outage still denies cleanly with AuthError
    rather than a raw 500 that says nothing about why.
    """
    checks = [principal.user_id]
    if principal.email and principal.email_verified:
        checks.append(principal.email)
    try:
        table = _table()
        for value in checks:
            resp = table.get_item(Key={"pk": ALLOWLIST_PK, "sk": f"ENTRY#{_allowlist_key(value)}"})
            if "Item" in resp:
                return True
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        return False
    return False


def assert_owner(principal: Principal) -> None:
    """Server-side gate for a private workspace.

    Separate from the token check on purpose: a valid Auth0 token proves who
    someone is, not that they are allowed here. With neither env list
    configured this is open — appropriate for a single-tenant private
    deployment (and for local/test setups with nothing configured at all),
    checked first and exactly as before so a fresh, unconfigured deploy's
    very first sign-in is unaffected by any of what follows.

    Once either env list is set, three ways in: the env allowlist (unchanged,
    for the original operator), the DB-backed allowlist (`allow`/`disallow`,
    reachable once *any* owner is signed in -- the API route sits behind this
    same check), or an unverified-nothing: an unverified email is a claim,
    not an identity, and is never enough on its own for either list.
    """
    subs, emails = owner_subjects(), owner_emails()
    if not subs and not emails:
        return
    if principal.user_id in subs:
        return
    if (principal.email and principal.email_verified
            and principal.email.lower() in emails):
        return
    if _is_allowed_in_table(principal):
        return
    raise AuthError("not an owner of this workspace")
