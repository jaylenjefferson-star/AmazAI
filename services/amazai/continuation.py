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


def is_approval_resume(event: dict) -> bool:
    """Whether this invocation continues a run that paused for a decision.

    A retry is also sent with `resume: True` (`orchestrator._reinvoke`), so that
    flag alone cannot say which this is; the decision's note is what separates them.
    Here, with the rest of the shape of a resume, so nothing outside this module
    reads it.
    """
    return bool(event.get("resume") and event.get("resumeNote"))


#: How much of one tool's result the model is handed back. A tool can return a whole
#: mailbox; the model needs enough to act on, not all of it.
MAX_RESULT_CHARS = 40_000


def _result_text(result: object) -> str:
    import json
    try:
        text = json.dumps(result, default=str)
    except (TypeError, ValueError):
        text = str(result)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + f" ... [cut: result was {len(text)} characters]"
    return text


def tool_round_messages(text: str, calls: list[dict]) -> list[dict]:
    """The turns that hand an inline tool's result back to the model, so it can go on.

    An inline function ends the model's stream at the call: the harness returns the
    request and waits for the answer. Something has to run the tool and send that answer
    on the same session, or the model's turn simply stops where it asked. Approvals
    have always done this through `resume_messages`; every other inline tool (a
    connector read, a Bot being created, a message to a teammate) needs the same,
    in the same shape, which is why it lives here and not in the loop.

    `text` is what the model said before it called, kept so it is not asked to repeat
    itself. Each call is `{"toolUseId", "name", "input", "result", "error"}`.
    """
    if mode() is Mode.RESUME_NOTE:
        lines = [f"{c['name']} -> {_result_text(c['result'])}" for c in calls]
        turns = [{"role": "assistant", "content": [{"text": text}]}] if text.strip() else []
        return turns + [{"role": "user", "content": [
            {"text": "Results of the tools you just called:\n" + "\n".join(lines)}]}]

    assistant: list[dict] = [{"text": text}] if text.strip() else []
    assistant += [{"toolUse": {"toolUseId": c["toolUseId"], "name": c["name"],
                               "input": c.get("input") or {}}} for c in calls]
    user = [{"toolResult": {"toolUseId": c["toolUseId"],
                            "status": "error" if c.get("error") else "success",
                            "content": [{"text": _result_text(c["result"])}]}} for c in calls]
    return [{"role": "assistant", "content": assistant}, {"role": "user", "content": user}]


def resume_messages(event: dict) -> list[dict]:
    """The turn(s) to append to history when a paused run resumes.

    Empty for a fresh run. Fails closed on `TOOL_RESULT` until the spike has
    shown the service accepts it (see `scripts/spike_d4.py`).
    """
    if not (event.get("resume") and event.get("resumeNote")):
        return []
    if mode() is Mode.TOOL_RESULT:
        approval = event.get("resumeApproval") or {}
        tool_use_id = approval.get("toolUseId")
        tool_name = approval.get("toolName")
        if not tool_use_id or not tool_name:
            raise ContinuationUnavailable("native continuation is missing the paused tool identity")
        status = "success" if approval.get("status") == "approved" else "error"
        return [
            {"role": "assistant", "content": [{"toolUse": {
                "toolUseId": tool_use_id, "name": tool_name,
                "input": approval.get("toolInput") or {}}}]},
            {"role": "user", "content": [{"toolResult": {
                "toolUseId": tool_use_id, "status": status,
                "content": [{"text": event["resumeNote"]}]}}]},
        ]
    return [{"role": "user", "content": [{"text": event["resumeNote"]}]}]
