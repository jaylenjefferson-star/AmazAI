"""Secret material, fetched at use and never carried further.

Two rules, both of which exist because the thing on the other side of this
module is a language model:

1. A secret is read from AWS Secrets Manager and returned to the caller that
   asked for it. It is never written to a store row, never attached to an
   agent, never put in an event, and never logged — not even truncated, since
   a prefix of an OAuth client secret is still a prefix of an OAuth client
   secret.

2. Nothing here is ever reachable from a tool an agent can call. The only
   callers are the control plane's own code paths.

Values are cached in the Lambda's memory for the life of the container, which
is a deliberate trade: a warm invocation avoids a Secrets Manager call, and
the blast radius is a process that already holds the credential anyway.
"""

from __future__ import annotations

import json
import os
import time

import boto3

#: How long a cached secret is trusted before it is read again. Short enough
#: that a rotation takes effect without a deploy.
TTL_SECONDS = 300

_cache: dict[str, tuple[float, dict]] = {}


class SecretUnavailable(RuntimeError):
    """The secret is missing or malformed.

    Raised rather than falling back to an environment variable: a connector
    that silently runs on a stale credential from somewhere else is worse
    than one that does not run.
    """


def _client():
    return boto3.client("secretsmanager")


def get_json(secret_id: str, *, required: tuple[str, ...] = ()) -> dict:
    """Read a JSON secret, checking the keys the caller depends on.

    The key check is here rather than at the call site so a half-filled
    secret fails at the boundary, naming the missing field, instead of
    surfacing later as a 401 from a third party.
    """
    now = time.monotonic()
    cached = _cache.get(secret_id)
    if cached and now - cached[0] < TTL_SECONDS:
        value = cached[1]
    else:
        try:
            raw = _client().get_secret_value(SecretId=secret_id)["SecretString"]
        except Exception as exc:  # noqa: BLE001
            raise SecretUnavailable(f"could not read secret {secret_id!r}") from exc
        try:
            value = json.loads(raw)
        except ValueError as exc:
            raise SecretUnavailable(
                f"secret {secret_id!r} is not JSON"
            ) from exc
        _cache[secret_id] = (now, value)

    missing = [k for k in required if not value.get(k)]
    if missing:
        # Names the keys, never the values.
        raise SecretUnavailable(
            f"secret {secret_id!r} is missing: {', '.join(missing)}"
        )
    return value


def composio_api_key() -> str:
    """The Composio project key. Secrets Manager only -- never source, never an
    environment variable, never chat.

    The secret is JSON, `{"api_key": "..."}`, so a half-filled one fails here
    naming the missing field rather than later as a 401 from a third party.
    """
    secret_id = os.environ.get("COMPOSIO_SECRET_ID", "amazai/composio")
    return get_json(secret_id, required=("api_key",))["api_key"]


def reset_cache() -> None:
    """Drop cached secrets. For tests, and for a forced re-read after
    rotation."""
    _cache.clear()
