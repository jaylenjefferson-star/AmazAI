"""Redaction of tool results before they enter model context.

Implements boundary B3 in `docs/architecture/01-identity-and-boundaries.md`.

This is a BACKSTOP, not the primary control. The primary control is
architectural: connector tokens live in the AgentCore Identity vault and are
attached at egress, so they never materialise in the runtime at all. This
module catches what leaks past that anyway -- a provider echoing a header, a
verbose error, a cookie in a redirect.

Treating it as the primary defense would be a mistake: a deny-list never sees
the secret shape it was not written for.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[redacted]"

#: Keys whose values are replaced wholesale, matched case-insensitively.
SENSITIVE_KEYS: frozenset[str] = frozenset({
    "authorization", "proxy-authorization", "cookie", "set-cookie",
    "x-api-key", "api_key", "apikey", "access_token", "refresh_token",
    "id_token", "client_secret", "password", "passwd", "secret",
    "private_key", "session_token", "aws_secret_access_key",
    "aws_session_token", "mfa_code", "otp", "totp",
})

_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
)

#: A vault reference is a placeholder, not a secret. Redacting it would hide
#: the very thing that proves the token never entered the runtime.
_VAULT_REF = re.compile(r"\$\{arn:aws:bedrock-agentcore:[^}]+\}")
_VAULT_SENTINEL = "\x00VAULTREF\x00"


def _scrub_text(text: str, found: list[str]) -> str:
    vault_refs: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        vault_refs.append(m.group(0))
        return _VAULT_SENTINEL

    text = _VAULT_REF.sub(_stash, text)

    for label, pattern in _VALUE_PATTERNS:
        if pattern.search(text):
            found.append(label)
            text = pattern.sub(REDACTED, text)

    for ref in vault_refs:
        text = text.replace(_VAULT_SENTINEL, ref, 1)
    return text


def redact(value: Any, *, _path: str = "") -> tuple[Any, list[str]]:
    """Return `(redacted_value, paths_redacted)`.

    The paths list is recorded as evidence, so you can see *that* something was
    withheld without seeing what.
    """
    found: list[str] = []

    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for k, v in value.items():
            path = f"{_path}.{k}" if _path else str(k)
            if isinstance(k, str) and k.lower() in SENSITIVE_KEYS:
                out[k] = REDACTED
                found.append(path)
            else:
                sub, sub_found = redact(v, _path=path)
                out[k] = sub
                found.extend(sub_found)
        return out, found

    if isinstance(value, (list, tuple)):
        items = []
        for i, v in enumerate(value):
            sub, sub_found = redact(v, _path=f"{_path}[{i}]")
            items.append(sub)
            found.extend(sub_found)
        return (type(value)(items) if isinstance(value, tuple) else items), found

    if isinstance(value, str):
        labels: list[str] = []
        scrubbed = _scrub_text(value, labels)
        if labels:
            found.extend(f"{_path}:{lbl}" if _path else lbl for lbl in labels)
        return scrubbed, found

    return value, found
