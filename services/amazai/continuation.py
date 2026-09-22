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

#: How much of one *held* result is kept for a later replay. A pause writes this to
#: storage and a resume sends it again, so it is bounded harder than a live result.
PAUSED_RESULT_CHARS = 4_000

#: And how much of one held call's *arguments*. The service pairs a result to its
#: call by id, not by argument fidelity, and a row that will not write is worse
#: than one that says it shortened something: see `paused_turn_calls`.
PAUSED_INPUT_CHARS = 4_000

#: Earlier rounds of a paused turn kept for its replay, after `compact`. Bounded
#: well under DynamoDB's 400 KB item limit, because this whole record has to fit
#: in one row alongside the paused round itself.
PAUSED_CARRIED_CHARS = 60_000

#: Characters of carried tool results one turn may re-send before the oldest of them
#: start being shortened. Every round of a turn re-sends every earlier round, so a
#: forty-round turn otherwise ends up sending the same megabyte forty times -- which
#: is slow for no gain: the model has already acted on those early results.
MAX_CARRIED_CHARS = 120_000

#: What an already-acted-on result is shortened to once the budget above is passed.
TRIMMED_RESULT_CHARS = 1_500

#: Written into a result that `compact` has already shortened, so a later round does
#: not shorten it again -- which would report the trimmed length as the original and
#: leave the budget unreachable.
TRIM_MARK = " [earlier in this turn; "


def result_text(result: object, *, limit: int = MAX_RESULT_CHARS) -> str:
    import json
    try:
        text = json.dumps(result, default=str)
    except (TypeError, ValueError):
        text = str(result)
    if len(text) > limit:
        text = text[:limit] + f" ... [cut: result was {len(text)} characters]"
    return text


def _call_text(call: dict) -> str:
    """One call's result as the text of a `toolResult` block.

    `text` is an already-rendered result -- a replayed one read back from
    storage, or the operator's decision note, which is a sentence and must not
    arrive JSON-quoted.
    """
    if call.get("text") is not None:
        return str(call["text"])
    return result_text(call.get("result"))


def _answerable(calls: list[dict]) -> list[dict]:
    """The calls that can be paired with a result, and a note about any that cannot.

    A `toolResult` is matched to its `toolUse` by id. A call whose id never
    arrived on the stream cannot be answered, and sending `toolUseId: ""`
    fails the whole turn rather than that one block -- so it is left out, and
    the omission is logged where the rest of the round still gets through.
    """
    import json
    keep, dropped = [], []
    for call in calls:
        (keep if (call.get("toolUseId") or "").strip() else dropped).append(call)
    if dropped:
        # Structural only. Never the arguments: a tool's input can hold a
        # brief, a message or a connector payload.
        print(json.dumps({"event": "continuation.tool_use_id_missing",
                          "tools": [c.get("name", "") for c in dropped]}))
    return keep


def _tool_turns(calls: list[dict], said: str = "") -> list[dict]:
    """The assistant/user pair that answers a set of inline calls.

    Every call the model made in one turn is answered here, in the order it
    made them. That completeness is the contract, not a nicety: the service
    validates the results against the ids it handed out, and one unanswered id
    rejects the turn with `Inline function result is missing toolUseId`.
    """
    calls = _answerable(calls)
    if not calls:
        return []
    assistant: list[dict] = [{"text": said}] if said.strip() else []
    assistant += [{"toolUse": {"toolUseId": c["toolUseId"], "name": c["name"],
                               "input": c.get("input") or {}}} for c in calls]
    user = [{"toolResult": {"toolUseId": c["toolUseId"],
                            "status": "error" if c.get("error") else "success",
                            "content": [{"text": _call_text(c)}]}} for c in calls]
    return [{"role": "assistant", "content": assistant}, {"role": "user", "content": user}]


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
        lines = [f"{c['name']} -> {_call_text(c)}" for c in calls]
        turns = [{"role": "assistant", "content": [{"text": text}]}] if text.strip() else []
        return turns + [{"role": "user", "content": [
            {"text": "Results of the tools you just called:\n" + "\n".join(lines)}]}]

    return _tool_turns(calls, text)


def _trimmed_input(raw: object) -> dict:
    """One call's arguments, small enough to store beside every other call's.

    Shortened rather than dropped by count. Whichever calls a turn made, *all*
    of their ids have to come back with a result, so nothing here may be a
    positional cut: losing the tail of a turn is the failure this record exists
    to prevent. The arguments are what is expensive, and the service pairs a
    result to its call by id, so this is the safe thing to make lossy.
    """
    import json
    value = raw if isinstance(raw, dict) else {}
    try:
        encoded = json.dumps(value, default=str)
    except (TypeError, ValueError):
        encoded = ""
    if len(encoded) <= PAUSED_INPUT_CHARS:
        return value
    return {"_shortened": f"{len(encoded)} characters of arguments, not kept while this "
                          "turn waited for a decision"}


def paused_turn_calls(calls: list[dict]) -> list[dict]:
    """One paused turn, small enough to store and replay.

    A turn that stops for a decision has to be answered *whole* when it resumes,
    so every call is kept -- the ones that already ran, the one that paused, and
    the ones after it. Their results are already spent (the model asked, the code
    answered), so only enough is kept to remind it what came back.
    """
    kept: list[dict] = []
    for call in calls:
        entry = {"toolUseId": call.get("toolUseId", ""), "name": call.get("name", ""),
                 "input": _trimmed_input(call.get("input"))}
        if call.get("approval"):
            # The decision itself. Its result does not exist yet; it is the note
            # the operator's answer produces, filled in by `resume_messages`.
            entry["approval"] = True
        else:
            entry["error"] = bool(call.get("error"))
            entry["text"] = _call_text(call)[:PAUSED_RESULT_CHARS]
        kept.append(entry)
    return kept


def without_text(turns: list[dict]) -> list[dict]:
    """The same turns with the model's prose removed, keeping every tool block.

    For storing the earlier rounds of a paused turn. What the model *said* across
    those rounds is already one persisted assistant message, and `build_messages`
    replays it from there; sending it again inside the replayed rounds would have
    the run read its own reply twice. The `toolUse`/`toolResult` blocks are the
    part that is not stored anywhere else, and the part the service counts.
    """
    out = []
    for row in turns:
        content = [b for b in row.get("content", []) if "text" not in b]
        if content:
            out.append({**row, "content": content})
    return out


def _decided_call(event: dict) -> dict:
    approval = event.get("resumeApproval") or {}
    tool_use_id = approval.get("toolUseId")
    tool_name = approval.get("toolName")
    if not tool_use_id or not tool_name:
        raise ContinuationUnavailable("native continuation is missing the paused tool identity")
    return {"toolUseId": tool_use_id, "name": tool_name,
            "input": approval.get("toolInput") or {},
            "text": event["resumeNote"],
            "error": approval.get("status") != "approved"}


def resume_messages(event: dict, paused_turn: dict | None = None) -> list[dict]:
    """The turn(s) to append to history when a paused run resumes.

    Empty for a fresh run. Fails closed on `TOOL_RESULT` until the spike has
    shown the service accepts it (see `scripts/spike_d4.py`).

    `paused_turn` is what the interrupted turn actually asked for, recorded when
    it paused. The model can ask for several things at once, and the decision may
    only be one of them; on the same runtime session the service still expects a
    result for every id it handed out, so all of them are replayed here with the
    decision in its own place. Replaying the approval alone is what produced
    `Inline function result is missing toolUseId` -- a hard stop caused by this
    module's bookkeeping, not by anything the Bot did. Absent (an older pause, or
    a turn whose only call was the approval) it falls back to that one pair.

    `carried` on that record is the *earlier rounds* of the same turn, which the
    loop holds in memory and would otherwise lose at the pause. They come first,
    in order, so a turn that paused on its third round resumes as the whole turn
    rather than as its last third -- otherwise the model is free to repeat calls
    it already made, having no record that it made them.
    """
    if not (event.get("resume") and event.get("resumeNote")):
        return []
    if mode() is not Mode.TOOL_RESULT:
        return [{"role": "user", "content": [{"text": event["resumeNote"]}]}]

    decided = _decided_call(event)
    earlier = (paused_turn or {}).get("carried") or []
    recorded = (paused_turn or {}).get("calls") or []
    calls, placed = [], False
    for call in recorded:
        if call.get("approval") or call.get("toolUseId") == decided["toolUseId"]:
            if placed:
                continue
            calls.append(decided)
            placed = True
        else:
            calls.append(call)
    if not placed:
        calls.append(decided)

    # Never silently lose the decision: if every recorded id was unusable the
    # approval's own pair is still a valid turn, and is better than no answer.
    return list(earlier) + (_tool_turns(calls) or _tool_turns([decided]))


def compact(turns: list[dict], *, budget: int = MAX_CARRIED_CHARS) -> list[dict]:
    """Keep the tool results one turn carries forward inside a payload budget.

    Each round of a turn re-sends every earlier round of the same turn, so the
    request grows with every tool call and a long turn spends real time uploading
    results the model read ten rounds ago. Past `budget` the oldest results are
    shortened, oldest first, until the payload fits.

    Only ever called on rounds the model has **already been given** -- never on
    the round being added. Shortening a result the model has not read yet, and
    labelling it as one it already acted on, would be a lie told to save bytes.

    Only the *text* of a result is shortened, and only once: a result that already
    carries the mark is left alone, because trimming a trimmed result reports the
    trimmed length as the original and never converges. Every `toolUse`/
    `toolResult` pair and every id survives untouched, because the service
    validates the pairing and a dropped block fails the whole turn -- the same
    failure this module exists to prevent.
    """
    import json

    def text_of(block: dict) -> str:
        content = (block.get("toolResult") or {}).get("content") or []
        return content[0].get("text", "") if content else ""

    def weight(block: dict) -> int:
        """What this block costs to send. A call's arguments are re-sent on every
        round exactly as its result is, so a budget that counted only results
        would pass a turn whose bulk is a pasted document in an argument."""
        if "toolUse" in block:
            try:
                return len(json.dumps(block["toolUse"].get("input") or {}, default=str))
            except (TypeError, ValueError):
                return 0
        return len(text_of(block))

    def size(rows: list[dict]) -> int:
        return sum(weight(block) for row in rows for block in row.get("content", []))

    if size(turns) <= budget:
        return turns

    out = [{**row, "content": [dict(b) for b in row.get("content", [])]} for row in turns]
    for row in out:
        for block in row["content"]:
            if size(out) <= budget:
                return out
            text = text_of(block)
            if len(text) <= TRIMMED_RESULT_CHARS or TRIM_MARK in text:
                continue
            block["toolResult"] = {**block["toolResult"], "content": [{"text": (
                text[:TRIMMED_RESULT_CHARS]
                + f" ...{TRIM_MARK}{len(text)} characters, shortened]")}]}
    return out
