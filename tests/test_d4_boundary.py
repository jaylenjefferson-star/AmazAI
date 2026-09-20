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

    def test_a_typo_is_an_error_not_a_silent_default(self, monkeypatch):
        monkeypatch.setenv("AMAZAI_CONTINUATION", "toolresult")
        with pytest.raises(ContinuationUnavailable):
            continuation.mode()

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
            "partial_json": json.dumps({"action": "spike.ping", "arguments": {"n": 1},
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

class FakeControl:
    def __init__(self, tools):
        self.tools, self.updated = tools, []

    def get_harness(self, harnessId):
        return {"harness": {"tools": self.tools}}

    def update_harness(self, **kw):
        self.updated.append(kw)


def core_with(tools):
    core = agentcore.AgentCore(runtime=object(), control=FakeControl(tools))
    return core


class TestExistingHarnessesAreReportedNotGuessedAt:
    OLD = [{"type": "inline_function", "name": n} for n in
           ("propose_agent", "request_approval", "handoff", "message_agent",
            "propose_skill", "remember", "propose_shared_memory")]

    def test_a_harness_from_before_the_new_tools_is_missing_exactly_them(self):
        report = core_with(self.OLD).missing_inline_tools("arn")
        assert report["known"] is True
        assert report["missing"] == ["propose_routine", "request_connector"]

    def test_a_current_harness_is_missing_nothing(self):
        every = [{"type": "inline_function", "name": n} for n in agentcore.INLINE_TOOLS]
        assert core_with(every).missing_inline_tools("arn")["missing"] == []

    def test_a_response_without_tools_is_reported_as_unknown_not_as_empty(self):
        core = agentcore.AgentCore(runtime=object(), control=type("C", (), {
            "get_harness": lambda self, harnessId: {"harness": {"name": "x"}}})())
        report = core.missing_inline_tools("arn")
        assert report["known"] is False and "keys were ['name']" in report["note"]

    def test_the_sync_merges_rather_than_replaces(self):
        """UpdateHarness replaces what it is given (BUILD_PLAN gotcha 3)."""
        control = FakeControl(self.OLD + [{"type": "agentcore_browser", "name": "browser"}])
        agentcore.AgentCore(runtime=object(), control=control).add_inline_tools("arn")
        sent = control.updated[0]["tools"]
        names = [t["name"] for t in sent]
        assert "browser" in names and "propose_routine" in names and "request_connector" in names
        assert len(names) == len(set(names)), "a tool was declared twice"
        assert control.updated[0]["harnessId"] == "arn"

    def test_an_arn_is_converted_to_the_control_plane_harness_id(self):
        control = FakeControl(self.OLD)
        core = agentcore.AgentCore(runtime=object(), control=control)
        core.add_inline_tools("arn:aws:bedrock-agentcore:us-west-2:1:harness/eng-123")
        assert control.updated[0]["harnessId"] == "eng-123"

    def test_the_report_alone_never_calls_update(self):
        script = load_script("sync_harness_tools")
        control = FakeControl(self.OLD)
        script.main(["--harness-arn", "arn"], core=agentcore.AgentCore(runtime=object(), control=control))
        assert control.updated == []


class TestTheFirstBotOffersWithRealTools:
    def test_the_brief_only_names_tools_that_exist(self):
        import re
        named = set(re.findall(r"\b(request_connector|propose_\w+)\b", onboarding.brief("Chief")))
        assert named, "the brief no longer tells the Bot how to make an offer"
        assert named <= set(agentcore.INLINE_TOOLS), named - set(agentcore.INLINE_TOOLS)

    def test_the_brief_says_to_deliver_first_and_offer_last(self):
        brief = onboarding.brief("Chief")
        assert brief.index("Deliver the result first") < brief.index("very last action")

    def test_every_offer_tool_is_actually_declared_on_new_harnesses(self):
        declared = {t["name"] for t in agentcore.harness_tools([]) if t["type"] == "inline_function"}
        assert {"request_connector", "propose_routine", "propose_agent", "propose_skill"} <= declared
