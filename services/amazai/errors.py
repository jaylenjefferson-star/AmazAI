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
    NEEDS_REPLAN = "needs_replan"    # hand back to the model, max 3
    NEEDS_HUMAN = "needs_human"      # pause, do not retry
    DIRTY_SESSION = "dirty_session"  # rotate the session and retry, max 2
    TERMINAL = "terminal"            # fail now, never retry


MAX_TRANSIENT_RETRIES = 3
# Bumped from 2 to 3: a re-plan that lands on a rotated/fresh session deserves
# a real second chance to recover before the run gives up, rather than burning
# its last attempt on the same conditions that produced the first failure.
MAX_REPLANS = 3
# A dangling toolUseId is fixed by rotating the session, not by re-planning or
# backing off, and the orchestrator has already rotated before the retry runs.
# So a small cap is enough: the first retry lands on a clean session, and a
# second occurrence means something other than the known dirty-session cause is
# at work, at which point failing beats looping.
MAX_DIRTY_SESSION_RETRIES = 2
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
# AgentCore reporting that this (agent, thread) pair's session already owes a
# toolResult it will never get: "Inline function result is missing toolUseId
# 'tooluse_...'", surfaced as an EventStreamError with a runtimeClientError
# shape. This is deterministically recoverable by rotating onto a fresh session
# (which the orchestrator does before the retry runs), so it must not fall
# through to the generic unrecognised-error branch that dead-ends as TERMINAL on
# the second attempt. See orchestrator._leaves_session_owing and
# runs.mark_session_dirty for the recovery machinery this classification unlocks.
_DIRTY_SESSION = re.compile(
    r"(?i)missing tooluseid|inline function result is missing"
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

    # Checked before transient so a dirty-session error is never mistaken for a
    # throttle and merely backed off without rotating (the retry would land on
    # the same owing session and fail identically, forever). Still after
    # terminal/needs-human, so the ordering contract for unrelated errors holds.
    if _DIRTY_SESSION.search(text):
        if attempt >= MAX_DIRTY_SESSION_RETRIES:
            return Classification(ErrorClass.TERMINAL, False, None,
                                  f"session stayed dirty past {MAX_DIRTY_SESSION_RETRIES} rotations")
        return Classification(ErrorClass.DIRTY_SESSION, True, None,
                              "session was owing a tool result, rotated and retrying")

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


#: The plain sentence an operator sees when a dirty-session error is surfaced as
#: a failure (retries exhausted, or a variant that could not recover). It names
#: what happened and what to do, without the raw runtimeClientError/toolUseId
#: text, which stays in evidence and logs for debugging.
_DIRTY_SESSION_SUMMARY = (
    "This conversation hit a snag and had to reset. Please send your message "
    "again."
)

#: The plain sentence for any other raw provider/runtime error that reaches the
#: operator surface. Deliberately generic: the specific cause is in evidence and
#: `lastError`, not in the operator's face.
_GENERIC_FAILURE_SUMMARY = (
    "Something went wrong while running this. The details were logged; please "
    "try again."
)

#: Raw markers that must never reach the operator verbatim. If a summary already
#: reads like a plain human sentence (no marker below), it is passed through
#: unchanged so intentional operator-facing messages are not rewritten.
_RAW_ERROR_MARKERS = re.compile(
    r"(?i)eventstreamerror|runtimeclienterror|missing tooluseid|"
    r"inline function result is missing|traceback|"
    # A "TypeName: message" head is how the orchestrator's broad except and
    # `_fail` format an exception; that shape is for logs, not for a person.
    r"^[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception):"
)


def humanize(message: str) -> str:
    """A plain, operator-facing sentence for a raw error string.

    The raw text still goes to evidence (`ev.error`) and structured logs; this
    only rewrites the one string the operator reads on a Blocked card so it is
    never the raw `EventStreamError ... missing toolUseId ...` or a stack. A
    message that already reads like a human sentence (an intentional summary,
    e.g. "cancelled by you while it was waiting") carries none of the markers
    below and is returned unchanged.
    """
    text = message or ""
    if _DIRTY_SESSION.search(text):
        return _DIRTY_SESSION_SUMMARY
    if _RAW_ERROR_MARKERS.search(text.strip()):
        return _GENERIC_FAILURE_SUMMARY
    return text
