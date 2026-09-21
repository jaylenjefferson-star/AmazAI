"""The console and the gateway must agree on which request headers exist.

A browser sends a preflight before any request that carries a header outside
the CORS-safelisted few. If the API Gateway allow-list does not name one, the
preflight comes back without `access-control-allow-origin`, the browser drops
the real request, and the person sees "Load failed" -- with nothing in the API's
logs, because the request never left the phone. That is exactly what stopped
every Bot creation in production: `idempotency-key` was sent and not allowed.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Headers a browser may send without asking; they never need listing.
SAFELISTED = {"accept", "accept-language", "content-language"}


def _allowed() -> set[str]:
    stack = (ROOT / "infra/lib/amazai-stack.ts").read_text()
    m = re.search(r"allowHeaders:\s*\[([^\]]*)\]", stack)
    assert m, "allowHeaders not found in the stack"
    return {h.strip().strip("'\"").lower() for h in m.group(1).split(",") if h.strip()}


def _sent() -> set[str]:
    """Every hyphenated lowercase key in api.js is a header: JSON body fields
    in this codebase are camelCase, so the two never collide."""
    src = (ROOT / "web/src/api.js").read_text()
    return {k for k in re.findall(r"['\"]([a-z][a-z0-9]*(?:-[a-z0-9]+)+)['\"]\s*:", src)}


def test_every_header_the_console_sends_is_allowed_by_the_gateway():
    missing = _sent() - _allowed() - SAFELISTED
    assert not missing, (
        f"console sends {sorted(missing)} but the API Gateway CORS allow-list does not "
        "name them; add them to allowHeaders in infra/lib/amazai-stack.ts")


def test_the_check_can_actually_see_the_idempotency_header():
    # Guards the guard: if the regex stopped matching, the test above would pass
    # on an empty set and protect nothing.
    assert "idempotency-key" in _sent()
    assert {"authorization", "content-type"} <= _allowed()
