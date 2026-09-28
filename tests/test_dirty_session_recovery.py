"""The dirty-session error recovers by rotation, and never surfaces raw.

AgentCore reports a session that already owes a toolResult with an
EventStreamError whose message is "Inline function result is missing toolUseId
'tooluse_...'". The recovery machinery (runs.mark_session_dirty, _mark_dirty,
_leaves_session_owing) already rotates the (agent, thread) pair onto a fresh
session before a same-run retry, but until this feature errors.classify() had
no pattern for that message: it fell through to the generic unrecognised-error
branch, which re-plans once and then dead-ends as TERMINAL. So the rotation
happened but the retry that would have used the clean session never ran, and
the raw EventStreamError text reached the operator's Blocked card verbatim.

What is proved here:
 1. classify() treats the "missing toolUseId" message as a retryable
    dirty-session error, not the generic terminal-on-second-attempt path.
 2. A run that hits this stream error rotates the session (epoch bumped and
    this run's own sessionId rewritten) and re-invokes the same run_id.
 3. When it is genuinely unrecoverable (retries exhausted) the operator-facing
    summary is a plain sentence with none of the raw markers, while the raw
    text is still kept on the run for diagnosis.
"""

import pytest

import handlers.orchestrator as orch
from amazai import keys as K, runs
from amazai.errors import ErrorClass, MAX_DIRTY_SESSION_RETRIES, classify, humanize
from amazai.states import RunState

from tests.test_agents_api import api_table, call  # noqa: F401
from tests.test_drive_loop import FakeCore, text, tool_use, usage, world  # noqa: F401


# The exact wording AWS sends, so the test breaks if the classifier or the
# humanizer is narrowed to a phrasing the service does not actually use.
DIRTY = ("EventStreamError (runtimeClientError) calling InvokeHarness: "
         "Inline function result is missing toolUseId 'tooluse_abc123'")


def session_of(world):  # noqa: F811
    return world.store.get(world.run["pk"], "META")["sessionId"]


def epoch_of(world):  # noqa: F811
    return runs.session_epoch(world.store, world.agent_id, world.run["threadId"])


class TestClassification:
    def test_the_dirty_session_message_is_retryable_not_generic_terminal(self):
        c = classify(DIRTY, attempt=0)
        assert c.cls is ErrorClass.DIRTY_SESSION
        assert c.retryable

    def test_it_still_retries_on_the_second_attempt_unlike_an_unknown_error(self):
        # An unrecognised error is TERMINAL by attempt 1; the dirty-session
        # error must not be, because the rotation only takes effect on retry.
        assert classify("something strange", attempt=1).cls is ErrorClass.TERMINAL
        assert classify(DIRTY, attempt=1).cls is ErrorClass.DIRTY_SESSION

    def test_it_is_bounded_and_gives_up_rather_than_looping(self):
        c = classify(DIRTY, attempt=MAX_DIRTY_SESSION_RETRIES)
        assert c.cls is ErrorClass.TERMINAL
        assert not c.retryable

    def test_the_shorter_inline_phrasing_also_matches(self):
        assert classify("Inline function result is missing").cls is ErrorClass.DIRTY_SESSION


class TestHumanize:
    def test_the_dirty_session_error_becomes_a_plain_sentence(self):
        out = humanize(DIRTY)
        for marker in ("EventStreamError", "runtimeClientError", "toolUseId",
                       "missing toolUseId", "Inline function result"):
            assert marker not in out
        assert "reset" in out.lower()

    def test_a_stack_or_exception_head_becomes_generic_not_raw(self):
        out = humanize("RuntimeError: boom\n  File ...\n  Traceback (most recent call last)")
        assert "RuntimeError" not in out and "Traceback" not in out

    def test_an_already_human_summary_is_left_alone(self):
        msg = "cancelled by you while it was waiting"
        assert humanize(msg) == msg


class TestEndToEndRotationAndRetry:
    def test_a_dirty_session_stream_error_rotates_and_reinvokes_the_same_run(self, world, monkeypatch):  # noqa: F811
        reinvoked = []
        monkeypatch.setattr(orch, "_reinvoke",
                            lambda run_id, owner_id, **kw: reinvoked.append(run_id))

        old_session = session_of(world)
        before = epoch_of(world)

        def dies(_kw):
            # A prior turn left this session owing an answer; this attempt hits
            # the dirty-session error before parsing any event of its own.
            yield {"runtimeClientError": {"message": DIRTY}}
        world.script(dies)

        result = world.drive()

        assert result["state"] == RunState.RETRYING.value
        assert epoch_of(world) == before + 1, "the pair was not rotated onto a fresh session"
        run = world.store.get(world.run["pk"], "META")
        assert run["sessionId"] != old_session, "the retry kept the poisoned session"
        assert run["sessionId"] == K.bot_session_id(
            world.store.owner_id, world.agent_id, world.run["threadId"], epoch=1)
        assert reinvoked == [world.run["runId"]], "the same run was not re-invoked"

    def test_the_error_raised_as_an_exception_recovers_the_same_way(self, world, monkeypatch):  # noqa: F811
        # The broad-except path (a Lambda-level raise) also sets dirty_exit via
        # _leaves_session_owing, so classify() must drive the same rotate+retry.
        monkeypatch.setattr(orch, "_reinvoke", lambda *a, **k: None)
        before = epoch_of(world)

        def dies(_kw):
            raise RuntimeError(DIRTY)
            yield  # noqa: unreachable, makes this a generator
        world.script(dies)

        result = world.drive()

        assert result["state"] == RunState.RETRYING.value
        assert epoch_of(world) == before + 1


class TestTheOperatorNeverSeesRawText:
    def test_when_it_finally_fails_the_summary_is_humanized_and_the_raw_text_is_kept(self, world, monkeypatch):  # noqa: F811
        monkeypatch.setattr(orch, "_reinvoke", lambda *a, **k: None)
        # Push it to the last allowed attempt so classify() returns TERMINAL and
        # the run settles FAILED instead of retrying again.
        world.store.update(world.run["pk"], "META", {"attempt": MAX_DIRTY_SESSION_RETRIES})

        def dies(_kw):
            yield {"runtimeClientError": {"message": DIRTY}}
        world.script(dies)

        result = world.drive()
        assert result["state"] == RunState.FAILED.value

        run = world.store.get(world.run["pk"], "META")
        summary = run["summary"]
        for marker in ("EventStreamError", "runtimeClientError", "toolUseId",
                       "missing toolUseId", "Inline function result"):
            assert marker not in summary, f"the operator would read raw {marker!r}"
        # The raw text is still recorded for diagnosis.
        assert "missing toolUseId" in run["lastError"]
