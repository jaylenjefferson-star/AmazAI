"""Decision D4, isolated.

Nothing here talks to AWS -- that is the point. Everything that can be verified
without credentials is verified: the continuation modes fail closed, the spike
draws the right conclusion from each possible answer the service could give, and
the harness-tool report reads what it is given without inventing a shape.
"""

import json

import pytest

from amazai import agentcore, continuation, d4_spike, onboarding
from amazai.continuation import ContinuationUnavailable, Mode

import importlib.util
from pathlib import Path


def load_script(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestContinuationFailsClosed:
    def test_a_fresh_run_has_nothing_to_resume(self):
        assert continuation.resume_messages({}) == []
        assert continuation.resume_messages({"resume": True}) == []

    def test_the_default_is_the_documented_fallback(self, monkeypatch):
        monkeypatch.delenv("AMAZAI_CONTINUATION", raising=False)
        assert continuation.mode() is Mode.RESUME_NOTE
        assert continuation.resume_messages({"resume": True, "resumeNote": "approved"}) == [
            {"role": "user", "content": [{"text": "approved"}]}]

    def test_tool_result_requires_the_paused_tool_identity(self, monkeypatch):
        monkeypatch.setenv("AMAZAI_CONTINUATION", "tool_result")
        with pytest.raises(ContinuationUnavailable, match="paused tool identity"):
            continuation.resume_messages({"resume": True, "resumeNote": "approved"})

    def test_tool_result_replays_the_paused_tool_and_decision(self, monkeypatch):
        monkeypatch.setenv("AMAZAI_CONTINUATION", "tool_result")
        turns = continuation.resume_messages({"resume": True, "resumeNote": "approved",
            "resumeApproval": {"status": "approved", "toolUseId": "tu-1",
                               "toolName": "request_approval", "toolInput": {"action": "x"}}})
        assert turns[0]["content"][0]["toolUse"]["toolUseId"] == "tu-1"
        assert turns[1]["content"][0]["toolResult"]["status"] == "success"

    def test_tool_result_answers_every_recorded_id_decision_in_its_own_slot(self, monkeypatch):
        """The id-completeness contract: the service holds every toolUseId it
        handed out for a turn and rejects the continuation if one comes back
        unanswered. A turn that asked for three things and paused on one must
        replay all three, the decision in the slot the model asked for it."""
        monkeypatch.setenv("AMAZAI_CONTINUATION", "tool_result")
        paused = {"calls": [
            {"toolUseId": "tu-a", "name": "read_artifact", "input": {"id": "art-1"},
             "error": False, "text": "the draft"},
            {"toolUseId": "tu-b", "name": "request_approval", "input": {"action": "email.send"},
             "approval": True},
            {"toolUseId": "tu-c", "name": "message_agent", "input": {"to": "ops"},
             "error": False, "text": "not run: paused"},
        ]}
        turns = continuation.resume_messages(
            {"resume": True, "resumeNote": "approved",
             "resumeApproval": {"status": "approved", "toolUseId": "tu-b",
                                "toolName": "request_approval",
                                "toolInput": {"action": "email.send"}}}, paused)

        assistant, user = turns[-2], turns[-1]
        answered = [b["toolResult"]["toolUseId"] for b in user["content"]]
        assert answered == ["tu-a", "tu-b", "tu-c"], "every recorded id, in order"
        # The decision sits in its own slot (tu-b), carrying the operator's note
        # and a success status because it was approved.
        decision = next(b["toolResult"] for b in user["content"]
                        if b["toolResult"]["toolUseId"] == "tu-b")
        assert decision["status"] == "success"
        assert decision["content"][0]["text"] == "approved"
        called = [b["toolUse"]["toolUseId"] for b in assistant["content"] if "toolUse" in b]
        assert called == ["tu-a", "tu-b", "tu-c"]

    def test_tool_result_puts_carried_earlier_rounds_first(self, monkeypatch):
        """A turn that paused on its third round resumes as the *whole* turn:
        the earlier rounds come first, in order, then the round that paused."""
        monkeypatch.setenv("AMAZAI_CONTINUATION", "tool_result")
        earlier = [
            {"role": "assistant", "content": [{"toolUse": {
                "toolUseId": "r1", "name": "read_artifact", "input": {}}}]},
            {"role": "user", "content": [{"toolResult": {
                "toolUseId": "r1", "status": "success", "content": [{"text": "round one"}]}}]},
        ]
        paused = {"carried": earlier, "calls": [
            {"toolUseId": "tu-b", "name": "request_approval", "input": {}, "approval": True}]}
        turns = continuation.resume_messages(
            {"resume": True, "resumeNote": "ok",
             "resumeApproval": {"status": "approved", "toolUseId": "tu-b",
                                "toolName": "request_approval"}}, paused)
        assert turns[0] is earlier[0] and turns[1] is earlier[1]
        assert turns[-1]["content"][0]["toolResult"]["toolUseId"] == "tu-b"

    def test_tool_result_falls_back_to_the_decisions_own_pair_if_no_ids_are_usable(self, monkeypatch):
        """Never silently lose the decision: if every recorded id was blank (so
        unanswerable) the approval's own pair is still emitted -- a valid turn,
        better than no answer at all."""
        monkeypatch.setenv("AMAZAI_CONTINUATION", "tool_result")
        paused = {"calls": [{"toolUseId": "", "name": "read_artifact",
                             "input": {}, "error": False, "text": "no id"}]}
        turns = continuation.resume_messages(
            {"resume": True, "resumeNote": "approved",
             "resumeApproval": {"status": "approved", "toolUseId": "tu-b",
                                "toolName": "request_approval"}}, paused)
        answered = [b["toolResult"]["toolUseId"] for b in turns[-1]["content"]]
        assert answered == ["tu-b"]

    def test_a_typo_is_an_error_not_a_silent_default(self, monkeypatch):
        monkeypatch.setenv("AMAZAI_CONTINUATION", "toolresult")
        with pytest.raises(ContinuationUnavailable):
            continuation.mode()

    def test_the_empty_string_is_the_default_not_a_typo(self, monkeypatch):
        # A var set-but-empty (a CDK context that resolved to nothing) is the
        # default, not a hard failure: the safety fallback must survive a blank.
        monkeypatch.setenv("AMAZAI_CONTINUATION", "")
        assert continuation.mode() is Mode.RESUME_NOTE

    def test_tool_result_is_never_reached_without_the_explicit_flag(self, monkeypatch):
        # The whole point of the seam: absent the explicit flag, a paused turn
        # that *could* be replayed as tool_result is still delivered as a note,
        # so no deploy ever runs the unverified shape by accident.
        monkeypatch.delenv("AMAZAI_CONTINUATION", raising=False)
        paused = {"calls": [{"toolUseId": "tu-1", "name": "request_approval",
                             "input": {}, "approval": True}]}
        turns = continuation.resume_messages(
            {"resume": True, "resumeNote": "approved",
             "resumeApproval": {"status": "approved", "toolUseId": "tu-1",
                                "toolName": "request_approval"}}, paused)
        assert turns == [{"role": "user", "content": [{"text": "approved"}]}]
        assert not any("toolUse" in b or "toolResult" in b
                       for t in turns for b in t["content"])

    def test_tool_round_messages_shape_follows_the_mode(self, monkeypatch):
        """The inline-tool round handed back mid-turn takes the same two shapes
        the resume does: a prose note by default, a real toolResult turn only
        when tool_result is configured."""
        calls = [{"toolUseId": "tu-1", "name": "read_artifact", "input": {"id": "a"},
                  "error": False, "result": {"ok": True}}]

        monkeypatch.delenv("AMAZAI_CONTINUATION", raising=False)
        note = continuation.tool_round_messages("looking", calls)
        assert not any("toolUse" in b or "toolResult" in b
                       for t in note for b in t["content"])

        monkeypatch.setenv("AMAZAI_CONTINUATION", "tool_result")
        native = continuation.tool_round_messages("looking", calls)
        assert native[0]["content"][-1]["toolUse"]["toolUseId"] == "tu-1"
        assert native[1]["content"][0]["toolResult"]["toolUseId"] == "tu-1"

    def test_nothing_outside_the_boundary_builds_a_resume_turn(self):
        """If another module read `resumeNote`, D4 would have two answers."""
        root = Path(__file__).resolve().parents[1] / "services"
        readers = [p.name for p in root.rglob("*.py")
                   if "resumeNote" in p.read_text() and p.name not in {"continuation.py", "api.py"}]
        assert readers == [], f"resume turns built outside continuation.py: {readers}"


# --- the spike, against every answer the service could give -------------------

def tool_use_stream(tool_use_id="tu-1"):
    return [
        {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {
            "toolUseId": tool_use_id, "name": "request_approval"}}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {
            "input": json.dumps({"action": "spike.ping", "arguments": {"n": 1},
                                 "why": "D4 spike"})}}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
    ]


def text_stream(text="Approved, thank you."):
    return [{"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": text}}}]


class FakeService:
    """Answers `invoke_harness` from a script: a list of (matcher, response|exception)."""

    def __init__(self, native="ok", note="ok", opens=True):
        self.native, self.note, self.opens, self.calls = native, note, opens, []

    def invoke_harness(self, **kw):
        self.calls.append(kw)
        content = [b for m in kw["messages"] for b in m["content"]]
        if any("toolResult" in b for b in content):
            return self._answer(self.native)
        if any("Your decision" in b.get("text", "") for b in content):
            return self._answer(self.note)
        return {"stream": tool_use_stream() if self.opens else text_stream("no tool")}

    @staticmethod
    def _answer(mode):
        if mode == "ok":
            return {"stream": text_stream()}
        if mode == "client_error":
            return {"stream": [{"runtimeClientError": {"message": "toolUse without toolResult"}}]}
        raise RuntimeError("ValidationException: unsupported content block")


class TestTheSpikeReadsEveryPossibleAnswer:
    def test_native_accepted_recommends_tool_result(self):
        assert d4_spike.run(FakeService("ok", "ok"), "arn", "m")["recommendation"] == "tool_result"

    def test_native_refused_but_note_accepted_recommends_the_fallback(self):
        report = d4_spike.run(FakeService("raise", "ok"), "arn", "m")
        assert report["recommendation"] == "resume_note"
        assert "ValidationException" in report["tool_result"]["detail"]

    def test_an_error_event_in_the_stream_counts_as_a_refusal(self):
        report = d4_spike.run(FakeService("client_error", "ok"), "arn", "m")
        assert report["tool_result"]["accepted"] is False
        assert "toolUse without toolResult" in report["tool_result"]["detail"]

    def test_neither_accepted_says_neither(self):
        assert d4_spike.run(FakeService("raise", "client_error"), "arn", "m")["recommendation"] == "neither"

    def test_a_model_that_never_calls_the_tool_is_reported_not_guessed_at(self):
        report = d4_spike.run(FakeService(opens=False), "arn", "m")
        assert report["tool_result"]["stage"] == "open"
        assert "did not call request_approval" in report["tool_result"]["detail"]

    def test_the_native_variant_sends_the_toolUse_it_is_answering(self):
        service = FakeService()
        d4_spike.try_native(service, "arn", "m")
        continuation_call = service.calls[-1]["messages"]
        assert continuation_call[1]["content"][0]["toolUse"]["toolUseId"] == "tu-1"
        assert continuation_call[2]["content"][0]["toolResult"]["toolUseId"] == "tu-1"

    def test_the_note_variant_is_what_the_orchestrator_sends_today(self):
        service = FakeService()
        d4_spike.try_note(service, "arn", "m")
        turn = service.calls[-1]["messages"][-1]
        assert turn["role"] == "user" and "Your decision" in turn["content"][0]["text"]
        assert not any("toolUse" in b for m in service.calls[-1]["messages"] for b in m["content"])

    def test_every_session_meets_the_length_the_service_requires(self):
        service = FakeService()
        d4_spike.run(service, "arn", "m")
        assert all(len(c["runtimeSessionId"]) >= 33 for c in service.calls)


class TestTheSpikeCommand:
    def test_without_execute_it_calls_nothing(self, capsys):
        script = load_script("spike_d4")
        service = FakeService()
        assert script.main(["--harness-arn", "arn", "--model-id", "m"], client=service) == 0
        assert service.calls == []
        assert "nothing was called" in capsys.readouterr().out

    def test_execute_prints_the_value_to_set(self, capsys):
        script = load_script("spike_d4")
        assert script.main(["--harness-arn", "arn", "--model-id", "m", "--execute"],
                           client=FakeService("raise", "ok")) == 0
        assert "AMAZAI_CONTINUATION=resume_note" in capsys.readouterr().out

    def test_neither_fails_the_command(self, capsys):
        script = load_script("spike_d4")
        assert script.main(["--harness-arn", "arn", "--model-id", "m", "--execute"],
                           client=FakeService("raise", "raise")) == 2


# --- harnesses made before the new inline tools ---------------------------------

class TestTheFirstBotOffersWithRealTools:
    def test_the_brief_only_names_tools_that_exist(self):
        import re
        named = set(re.findall(r"\b(request_connector|create_agent|propose_\w+)\b", onboarding.brief("Chief")))
        assert named, "the brief no longer tells the Bot how to make an offer"
        assert named <= set(agentcore.INLINE_TOOLS), named - set(agentcore.INLINE_TOOLS)

    def test_the_brief_says_to_deliver_first_and_offer_last(self):
        brief = onboarding.brief("Chief")
        assert brief.index("Deliver the result first") < brief.index("very last action")

    def test_every_offer_tool_is_actually_declared_on_new_harnesses(self):
        declared = {t["name"] for t in agentcore.harness_tools([]) if t["type"] == "inline_function"}
        assert {"request_connector", "propose_routine", "create_agent", "propose_skill"} <= declared
