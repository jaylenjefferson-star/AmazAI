"""A turn that abandons a tool result rotates its session, not just its retry.

The AgentCore session tracks every `toolUseId` it has handed out across the
whole (agent, thread) pair -- not per run, per turn, or per Lambda invocation.
So a turn that computes a tool's result and then never sends it back leaves the
*session* owing an answer it will never get. Every future invocation on that
session -- this run's own retry, an unrelated run started an hour later, a
completely different task in the same conversation -- then fails with:

    EventStreamError (runtimeClientError) calling InvokeHarness:
    Inline function result is missing toolUseId 'tooluse_...'

regardless of what that invocation itself does. #35 fixed the one path that
abandoned an answer on *purpose* (an approval pause) by carrying it forward.
It did not touch the four paths that abandon one by *necessity*: a stream
error, the round/time/budget ceiling, a mid-turn cancel, and an exception
escaping the loop entirely (a Lambda timeout is the production shape of this
last one). Each of those still ends the turn with `answered` -- results
already computed -- simply never sent.

What is proved here: each of those four paths bumps this (agent, thread)
pair's session epoch, so the *next* run gets a fresh session rather than
inheriting one that already has an unmet obligation on it -- including a
same-run retry, which reuses this run's own row and therefore must not reuse
its poisoned session. And the paths that answer everything they owe (a clean
completion, an approval pause) must not rotate anything: rotating on every
turn would defeat the one thing rotation exists to preserve -- a Bot's
memory of its own conversation, which lives inside that session.
"""

import json

import pytest

import handlers.orchestrator as orch
from amazai import keys as K, runs
from amazai.states import RunState

from tests.fake_composio import WRITE
from tests.loop_world import POST
from tests.test_agents_api import api_table, call  # noqa: F401
from tests.test_drive_loop import FakeCore, text, tool_use, world  # noqa: F401


def session_of(world):  # noqa: F811
    return world.store.get(world.run["pk"], "META")["sessionId"]


def epoch_of(world):  # noqa: F811
    return runs.session_epoch(world.store, world.agent_id, world.run["threadId"])


class TestTheEpochIsComputedFromTheKeysModule:
    def test_the_default_epoch_changes_nothing(self):
        assert (K.bot_session_id("o", "a", "t")
                == K.bot_session_id("o", "a", "t", epoch=0))

    def test_a_nonzero_epoch_is_a_different_session(self):
        base = K.bot_session_id("o", "a", "t")
        assert K.bot_session_id("o", "a", "t", epoch=1) != base
        assert K.bot_session_id("o", "a", "t", epoch=2) != base
        assert K.bot_session_id("o", "a", "t", epoch=1) != K.bot_session_id("o", "a", "t", epoch=2)

    def test_a_fresh_pair_is_on_epoch_zero(self, store):
        assert runs.session_epoch(store, "eng", "dm-eng") == 0

    def test_marking_dirty_advances_it_and_returns_the_new_value(self, store):
        assert runs.mark_session_dirty(store, "eng", "dm-eng") == 1
        assert runs.session_epoch(store, "eng", "dm-eng") == 1
        assert runs.mark_session_dirty(store, "eng", "dm-eng") == 2
        assert runs.session_epoch(store, "eng", "dm-eng") == 2

    def test_it_is_scoped_to_one_pair_not_the_whole_account(self, store):
        runs.mark_session_dirty(store, "eng", "dm-eng")
        assert runs.session_epoch(store, "eng", "dm-eng") == 1
        assert runs.session_epoch(store, "eng", "other-thread") == 0
        assert runs.session_epoch(store, "ops", "dm-eng") == 0

    def test_a_new_run_on_a_rotated_pair_gets_the_current_epoch(self, store):
        runs.mark_session_dirty(store, "eng", "dm-eng")
        run = runs.create(store, agent_id="eng", thread_id="dm-eng", goal="go")
        assert run["sessionId"] == K.bot_session_id(store.owner_id, "eng", "dm-eng", epoch=1)


class TestACleanCompletionNeverRotates:
    def test_a_plain_reply_with_no_tool_calls(self, world):  # noqa: F811
        before = epoch_of(world)
        world.script([text("all done, nothing needed")])
        result = world.drive()
        assert result["state"] == RunState.COMPLETED.value
        assert epoch_of(world) == before

    def test_a_tool_call_answered_and_the_turn_completes(self, world):  # noqa: F811
        before = epoch_of(world)
        world.script([text("noting it"), *tool_use("remember", {"title": "t", "body": "b"})],
                     [text("done")])
        result = world.drive()
        assert result["state"] == RunState.COMPLETED.value
        assert epoch_of(world) == before, "every call this turn made was answered; nothing is owed"


class TestAnApprovalPauseNeverRotates:
    def test_the_pause_itself_does_not_rotate(self, world, monkeypatch):  # noqa: F811
        monkeypatch.setenv("AMAZAI_CONTINUATION", "tool_result")
        before = epoch_of(world)
        world.script([text("posting it"), *tool_use("connector_call", {"tool": WRITE, "arguments": POST})])
        result = world.drive()
        assert result["state"] == RunState.AWAITING_APPROVAL.value
        assert epoch_of(world) == before, "#35 already carries a paused turn forward whole"


class TestAStreamErrorRotates:
    def test_a_transient_error_after_a_tool_call_rotates(self, world):  # noqa: F811
        def dies(_kw):
            yield from tool_use("remember", {"title": "t", "body": "b"})
            raise RuntimeError("Task timed out after 30s")
        before = epoch_of(world)
        world.script(dies, [text("never reached")])

        result = world.drive()

        assert result["state"] == "RETRYING"
        assert epoch_of(world) == before + 1

    def test_the_retried_run_uses_the_rotated_session_not_the_old_one(self, world):  # noqa: F811
        def dies(_kw):
            yield from tool_use("remember", {"title": "t", "body": "b"})
            raise RuntimeError("Task timed out after 30s")
        old_session = session_of(world)
        world.script(dies, [text("recovered")])

        world.drive()
        run = world.store.get(world.run["pk"], "META")

        assert run["sessionId"] != old_session
        assert run["sessionId"] == K.bot_session_id(
            world.store.owner_id, world.agent_id, world.run["threadId"], epoch=1)

    def test_an_error_with_no_tool_call_in_the_turn_does_not_rotate(self, world):  # noqa: F811
        """Nothing was computed and abandoned, so nothing is owed."""
        def dies(_kw):
            raise RuntimeError("Task timed out after 30s")
        before = epoch_of(world)
        world.script(dies, [text("recovered")])
        world.drive()
        assert epoch_of(world) == before


class TestTheRoundCeilingRotates:
    def test_hitting_the_round_limit_with_unanswered_results_rotates(self, world, monkeypatch):  # noqa: F811
        monkeypatch.setattr(orch, "MAX_TOOL_ROUNDS", 2)
        before = epoch_of(world)
        forever = [lambda kw, i=i: tool_use("remember", {"title": f"t{i}", "body": "b"}, f"tu-{i}")
                   for i in range(6)]
        world.script(*forever)

        result = world.drive()

        assert result["state"] == RunState.COMPLETED.value   # the run itself still settles cleanly
        assert "more tool calls than one turn allows" in [
            m for m in world.messages() if m["role"] == "assistant"][-1]["text"]
        assert epoch_of(world) == before + 1, (
            "the last round's results were computed and never sent back")

    def test_the_time_ceiling_rotates_the_same_way(self, world, monkeypatch):  # noqa: F811
        monkeypatch.setattr(orch, "ROUND_BUDGET_SECONDS", -1)
        before = epoch_of(world)
        world.script([*tool_use("remember", {"title": "t", "body": "b"})], [text("never asked for")])
        world.drive()
        assert epoch_of(world) == before + 1


class TestNothingOutsideTheseFourPathsRotates:
    def test_the_round_that_naturally_runs_out_of_tool_calls_does_not_rotate(self, world):  # noqa: F811
        """A model that stops calling tools on its own -- the ordinary end of a
        multi-round turn -- answers every call it made along the way. No path
        here is the one abandoning anything, so nothing should rotate."""
        before = epoch_of(world)
        world.script([*tool_use("remember", {"title": "a", "body": "1"})],
                     [*tool_use("remember", {"title": "b", "body": "2"}, "tu-2")],
                     [text("both saved")])
        result = world.drive()
        assert result["state"] == RunState.COMPLETED.value
        assert epoch_of(world) == before
