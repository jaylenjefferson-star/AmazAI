"""Error classification for bounded retry.

Implements the retry table in `docs/architecture/05-run-lifecycle.md`.
"Flake" is not a root cause: a class is assigned from the error itself, and
`terminal` errors are never retried.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class ErrorClass(str, Enum):
    TRANSIENT = "transient"          # backoff and retry, max 3
    NEEDS_REPLAN = "needs_replan"    # hand back to the model, max 2
    NEEDS_HUMAN = "needs_human"      # pause, do not retry
    TERMINAL = "terminal"            # fail now, never retry


MAX_TRANSIENT_RETRIES = 3
MAX_REPLANS = 2
BACKOFF_SECONDS = (2, 4, 8)

_TRANSIENT = re.compile(
    r"(?i)throttl|ratelimit|rate limit|timed? ?out|timeout|connection reset|"
    r"connection aborted|temporarily unavailable|service unavailable|"
    r"\b(?:500|502|503|504)\b"
)
_NEEDS_HUMAN = re.compile(
    r"(?i)\b401\b|\b403\b|unauthorized|forbidden|invalid_grant|token expired|"
    r"mfa|two[- ]factor|captcha|consent required|reauthenticat"
)
_TERMINAL = re.compile(
    r"(?i)grant denied|not granted|budget exceeded|invalid target|"
    r"never approvable|\b404\b|not found|no such|"
    # The shape of the conversation itself is wrong. Sent again unchanged it is
    # refused again, so re-planning only burns the attempts (and matches
    # "validation" below).
    r"assistant message prefill|must end with a user message|must alternate between"
)
_NEEDS_REPLAN = re.compile(
    r"(?i)validation|invalid (?:argument|parameter|input)|schema|"
    r"selector|element not found|assertion|test failed|\b400\b|\b422\b"
)


@dataclass(frozen=True)
class Classification:
    cls: ErrorClass
    retryable: bool
    backoff_seconds: int | None
    reason: str


def classify(message: str, *, attempt: int = 0) -> Classification:
    """Classify a tool or runtime error and say whether to retry.

    Order matters: terminal and needs-human are checked before transient, so a
    403 is never mistaken for a throttle and retried into a lockout.
    """
    text = message or ""

    if _TERMINAL.search(text):
        return Classification(ErrorClass.TERMINAL, False, None, "terminal error, no retry")

    if _NEEDS_HUMAN.search(text):
        return Classification(ErrorClass.NEEDS_HUMAN, False, None, "needs a human decision")

    if _TRANSIENT.search(text):
        if attempt >= MAX_TRANSIENT_RETRIES:
            return Classification(ErrorClass.TERMINAL, False, None,
                                  f"transient error persisted past {MAX_TRANSIENT_RETRIES} retries")
        return Classification(ErrorClass.TRANSIENT, True,
                              BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)],
                              "transient, backing off")

    if _NEEDS_REPLAN.search(text):
        if attempt >= MAX_REPLANS:
            return Classification(ErrorClass.TERMINAL, False, None,
                                  f"could not recover after {MAX_REPLANS} re-plans")
        return Classification(ErrorClass.NEEDS_REPLAN, True, None, "returning error to the model")

    # Unrecognised errors get one re-plan, then fail. Defaulting to "retry"
    # would turn an unknown error into a billable loop.
    if attempt >= 1:
        return Classification(ErrorClass.TERMINAL, False, None, "unrecognised error, not retrying again")
    return Classification(ErrorClass.NEEDS_REPLAN, True, None, "unrecognised error, one re-plan")
