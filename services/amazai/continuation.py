"""How a run continues after the model has asked for something.

This is the one place decision D4 lives. Everything else in the loop -- the
pause, the decision, the resume -- is the same either way; only the *shape of
the turn that resumes it* differs, and that shape is the thing nobody has been
able to verify without an AWS account.

    RESUME_NOTE   the decision is delivered as a synthetic user turn.
                  This is the documented fallback and what runs today.
    TOOL_RESULT   the decision is delivered as a `toolResult` for the
                  `toolUse` the model emitted, on the same `runtimeSessionId`.
                  Cleaner, and unverified.

Which one works is decided by running `scripts/spike_d4.py` against the real
service, not by this module. Until then `TOOL_RESULT` is refused rather than
guessed at -- an invented continuation shape fails in a way that looks like a
permissions error, which is the specific trap CLAUDE.md warns about.

Nothing else should read `resumeNote` or build a resume turn. If it did, D4
would have two answers.
"""

from __future__ import annotations

import os
from enum import Enum


class Mode(str, Enum):
    RESUME_NOTE = "resume_note"
    TOOL_RESULT = "tool_result"


class ContinuationUnavailable(RuntimeError):
    """The configured continuation mode has not been verified."""


def mode() -> Mode:
    raw = (os.environ.get("AMAZAI_CONTINUATION") or Mode.RESUME_NOTE.value).strip().lower()
    try:
        return Mode(raw)
    except ValueError as exc:
        raise ContinuationUnavailable(
            f"AMAZAI_CONTINUATION={raw!r}; expected one of {[m.value for m in Mode]}") from exc


def resume_messages(event: dict) -> list[dict]:
    """The turn(s) to append to history when a paused run resumes.

    Empty for a fresh run. Fails closed on `TOOL_RESULT` until the spike has
    shown the service accepts it (see `scripts/spike_d4.py`).
    """
    if not (event.get("resume") and event.get("resumeNote")):
        return []
    if mode() is Mode.TOOL_RESULT:
        raise ContinuationUnavailable(
            "AMAZAI_CONTINUATION=tool_result has not been verified against "
            "invoke_harness (decision D4). Run scripts/spike_d4.py; until it "
            "reports tool_result as accepted, leave this at resume_note.")
    return [{"role": "user", "content": [{"text": event["resumeNote"]}]}]
