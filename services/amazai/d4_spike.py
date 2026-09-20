"""The D4 experiment: how does a paused run continue on the real service?

Decision D4 (docs/architecture/15-open-decisions.md) is the one fact the whole
pause/resume design rests on and the one nobody can verify without an AWS
account: after the model emits an `inline_function` tool call, does
`invoke_harness` accept a `toolResult` continuation on the same
`runtimeSessionId`, or must the decision go back as a plain user turn?

This module runs that experiment. It is pure with respect to AWS -- the client is
injected -- so every branch is tested here with a fake, and the only thing left
for a person to do is point it at a real harness (`scripts/spike_d4.py`).

It tests exactly what `continuation.py` would send, not a cleaned-up version of
it: a variant that passes here but differs from what the orchestrator sends
proves nothing.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field

from amazai.stream import EventKind, StreamParser

ACTION = "spike.ping"
PROMPT = (
    f'Call the request_approval tool exactly once, with action "{ACTION}", '
    'arguments {"n": 1} and why "D4 spike". Do not do anything else.'
)


@dataclass
class Drained:
    text: str = ""
    tool_uses: list = field(default_factory=list)
    error: str = ""


@dataclass
class Outcome:
    variant: str
    accepted: bool
    stage: str = ""          # "open" if the experiment could not start, else "continue"
    detail: str = ""         # what was wrong, verbatim, when it was not accepted
    replied: str = ""        # what the model said after the continuation

    def to_dict(self) -> dict:
        return asdict(self)


def _drain(response) -> Drained:
    parser = StreamParser()
    out = Drained()
    for raw in response["stream"]:
        for ev in parser.feed(raw):
            if ev.kind is EventKind.TEXT:
                out.text += ev.text
            elif ev.kind is EventKind.TOOL_USE:
                out.tool_uses.append(ev)
            elif ev.kind is EventKind.ERROR:
                out.error = out.error or ev.error
    for ev in parser.flush():
        out.tool_uses.append(ev)
    return out


def _invoke(client, harness_arn, model_id, session_id, messages) -> Drained:
    try:
        response = client.invoke_harness(
            harnessArn=harness_arn, runtimeSessionId=session_id, messages=messages,
            model={"bedrockModelConfig": {"modelId": model_id}},
            systemPrompt=[{"text": "You are a test harness. Follow the instruction exactly."}],
        )
        return _drain(response)
    except Exception as exc:  # noqa: BLE001 -- the error text is the finding
        return Drained(error=f"{type(exc).__name__}: {exc}")


def _new_session() -> str:
    return f"d4-spike-{uuid.uuid4().hex}"          # >= 33 characters, as the service requires


def _open(client, harness_arn, model_id):
    session = _new_session()
    first = [{"role": "user", "content": [{"text": PROMPT}]}]
    opened = _invoke(client, harness_arn, model_id, session, first)
    call = next((t for t in opened.tool_uses if t.tool_name == "request_approval"), None)
    return session, first, opened, call


def try_native(client, harness_arn, model_id) -> Outcome:
    """Continue with a `toolResult` for the `toolUse` the model emitted."""
    session, first, opened, call = _open(client, harness_arn, model_id)
    if opened.error:
        return Outcome("tool_result", False, "open", opened.error)
    if call is None:
        return Outcome("tool_result", False, "open",
                       "the model did not call request_approval, so there is nothing to continue")
    messages = first + [
        {"role": "assistant", "content": [{"toolUse": {
            "toolUseId": call.tool_use_id, "name": call.tool_name, "input": call.tool_input}}]},
        {"role": "user", "content": [{"toolResult": {
            "toolUseId": call.tool_use_id, "status": "success",
            "content": [{"text": f'Your decision on "{ACTION}": approved.'}]}}]},
    ]
    return _outcome("tool_result", _invoke(client, harness_arn, model_id, session, messages))


def try_note(client, harness_arn, model_id) -> Outcome:
    """Continue the way `continuation.RESUME_NOTE` does today: a plain user turn,
    with no `toolUse` in the history."""
    session, first, opened, call = _open(client, harness_arn, model_id)
    if opened.error:
        return Outcome("resume_note", False, "open", opened.error)
    if call is None:
        return Outcome("resume_note", False, "open",
                       "the model did not call request_approval, so there is nothing to continue")
    messages = first + [{"role": "user",
                         "content": [{"text": f'Your decision on "{ACTION}": approved.'}]}]
    return _outcome("resume_note", _invoke(client, harness_arn, model_id, session, messages))


def _outcome(variant: str, drained: Drained) -> Outcome:
    if drained.error:
        return Outcome(variant, False, "continue", drained.error)
    return Outcome(variant, True, "continue", "", drained.text.strip()[:200])


def recommend(native: Outcome, note: Outcome) -> str:
    """Which `AMAZAI_CONTINUATION` value the evidence supports.

    `tool_result` only if the native continuation was accepted; otherwise the
    note, if that works; otherwise neither -- and the design has a bigger problem
    than a choice of shape.
    """
    if native.accepted:
        return "tool_result"
    if note.accepted:
        return "resume_note"
    return "neither"


def run(client, harness_arn: str, model_id: str) -> dict:
    native = try_native(client, harness_arn, model_id)
    note = try_note(client, harness_arn, model_id)
    return {"tool_result": native.to_dict(), "resume_note": note.to_dict(),
            "recommendation": recommend(native, note)}
