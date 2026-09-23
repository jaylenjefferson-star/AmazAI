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
from tests.test_drive_loop import FakeCore, text, tool_use, usage, world  # noqa: F401


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
        assert epoch_of(world) == before + 1, (
            "the last round's results were computed and never sent back")

    def test_hitting_the_round_limit_with_auto_continues_exhausted_still_rotates(self, world, monkeypatch):  # noqa: F811
        """Rotation is about the abandoned tool results, not about whether the
        ceiling note gets said out loud -- must hold whichever text path runs."""
        monkeypatch.setattr(orch, "MAX_TOOL_ROUNDS", 2)
        world.store.update(world.run["pk"], "META",
                           {"trigger": {"type": "user", "autoContinued": orch.MAX_AUTO_CONTINUES}})
        before = epoch_of(world)
        forever = [lambda kw, i=i: tool_use("remember", {"title": f"t{i}", "body": "b"}, f"tu-{i}")
                   for i in range(6)]
        world.script(*forever)

        result = world.drive()

        assert result["state"] == RunState.COMPLETED.value
        assert "more tool calls than one turn allows" in [
            m for m in world.messages() if m["role"] == "assistant"][-1]["text"]
        assert epoch_of(world) == before + 1


class TestTheRoundCeilingAutoContinues:
    """A round/time ceiling is friction, not a stop: the task picks itself back
    up instead of waiting for the operator to say "continue" -- bounded to
    `MAX_AUTO_CONTINUES` in a row so a task that genuinely cannot finish does
    not run away with cost on its own."""

    def _continuation(self, world):  # noqa: F811
        rows = world.store.query_index(
            "gsi1", "gsi1pk", "RUNS",
            predicate=lambda r: (r.get("trigger") or {}).get("redirectOf") == world.run["runId"])
        assert len(rows) == 1, "expected exactly one queued continuation"
        return rows[0]

    def test_a_fresh_ceiling_hit_queues_a_continuation_instead_of_asking(self, world, monkeypatch):  # noqa: F811
        monkeypatch.setattr(orch, "MAX_TOOL_ROUNDS", 2)
        forever = [lambda kw, i=i: tool_use("remember", {"title": f"t{i}", "body": "b"}, f"tu-{i}")
                   for i in range(6)]
        world.script(*forever)

        world.drive()

        cont = self._continuation(world)
        assert cont["agentId"] == world.agent_id and cont["threadId"] == world.run["threadId"]
        assert cont["trigger"]["type"] == "user"          # still owner-asked; see _owner_asked
        assert cont["trigger"]["autoContinued"] == 1

    def test_the_continuation_count_climbs_and_stops_at_the_bound(self, world, monkeypatch):  # noqa: F811
        monkeypatch.setattr(orch, "MAX_TOOL_ROUNDS", 2)
        world.store.update(world.run["pk"], "META",
                           {"trigger": {"type": "user", "autoContinued": orch.MAX_AUTO_CONTINUES - 1}})
        forever = [lambda kw, i=i: tool_use("remember", {"title": f"t{i}", "body": "b"}, f"tu-{i}")
                   for i in range(6)]
        world.script(*forever)

        world.drive()

        cont = self._continuation(world)
        assert cont["trigger"]["autoContinued"] == orch.MAX_AUTO_CONTINUES

    def test_at_the_bound_it_stops_and_asks_instead_of_queuing_another(self, world, monkeypatch):  # noqa: F811
        monkeypatch.setattr(orch, "MAX_TOOL_ROUNDS", 2)
        world.store.update(world.run["pk"], "META",
                           {"trigger": {"type": "user", "autoContinued": orch.MAX_AUTO_CONTINUES}})
        forever = [lambda kw, i=i: tool_use("remember", {"title": f"t{i}", "body": "b"}, f"tu-{i}")
                   for i in range(6)]
        world.script(*forever)

        world.drive()

        rows = world.store.query_index(
            "gsi1", "gsi1pk", "RUNS",
            predicate=lambda r: (r.get("trigger") or {}).get("redirectOf") == world.run["runId"])
        assert rows == []
        assert "more tool calls than one turn allows" in [
            m for m in world.messages() if m["role"] == "assistant"][-1]["text"]

    def test_the_budget_ceiling_never_auto_continues(self, world, monkeypatch):  # noqa: F811
        """A budget stop is a real stop, never friction to smooth over -- auto
        continuing past it would spend past the ceiling it exists to enforce.

        Starts at $0 (so the start-of-run check passes) and crosses a tiny
        per-run ceiling once a round's usage event is parsed, exercising the
        in-loop `money.should_stop` path specifically."""
        monkeypatch.setattr(orch, "_budget_for", lambda agent: orch.Budget(
            per_run_usd=0.0000001, per_month_usd=100.0, on_ceiling="hard_stop"))
        forever = [lambda kw, i=i: [*tool_use("remember", {"title": f"t{i}", "body": "b"}, f"tu-{i}"),
                                    usage(input_tokens=1000, output_tokens=1000)]
                   for i in range(3)]
        world.script(*forever)

        world.drive()

        rows = world.store.query_index(
            "gsi1", "gsi1pk", "RUNS",
            predicate=lambda r: (r.get("trigger") or {}).get("redirectOf") == world.run["runId"])
        assert rows == []
        assert "I stopped here" in [m for m in world.messages() if m["role"] == "assistant"][-1]["text"]

    def test_the_time_ceiling_rotates_the_same_way(self, world, monkeypatch):  # noqa: F811
        monkeypatch.setattr(orch, "ROUND_BUDGET_SECONDS", -1)
        before = epoch_of(world)
        world.script([*tool_use("remember", {"title": "t", "body": "b"})], [text("never asked for")])
        world.drive()
        assert epoch_of(world) == before + 1


class TestAnUnfulfilledCommitmentAutoContinues:
    """A reply that promises action and calls no tool is indistinguishable
    from a genuinely finished one by `answered` alone -- both are empty, and
    no ceiling fires to explain it. Confirmed in production, twice, against
    the identical shape: "I'll do all of them now." and then nothing."""

    def _continuation(self, world):  # noqa: F811
        rows = world.store.query_index(
            "gsi1", "gsi1pk", "RUNS",
            predicate=lambda r: (r.get("trigger") or {}).get("redirectOf") == world.run["runId"])
        return rows

    def test_a_reply_that_promises_action_and_calls_nothing_auto_continues(self, world):  # noqa: F811
        world.script([text("Let me update every agent's description to reflect that. "
                           "I'll do all of them now.")])

        world.drive()

        rows = self._continuation(world)
        assert len(rows) == 1
        assert rows[0]["trigger"]["autoContinued"] == 1
        assert rows[0]["trigger"]["type"] == "user"

    def test_a_genuinely_finished_reply_does_not_auto_continue(self, world):  # noqa: F811
        world.script([text("Done -- the audit is above. Let me know what you'd like to prioritize.")])
        world.drive()
        assert self._continuation(world) == []

    def test_an_i_will_used_mid_explanation_does_not_trigger_if_the_reply_still_finishes(self, world):  # noqa: F811
        world.script([text("I'll note that this is a known limitation. "
                           "For now, the workaround is asking the operator directly.")])
        world.drive()
        assert self._continuation(world) == []

    def test_a_promise_that_the_next_round_never_follows_through_on_still_auto_continues(self, world):  # noqa: F811
        """The check reads the *whole* accumulated reply, not just the round
        that triggers it: a tool call and a promise can land in round 1
        ("I'll do the rest now."), `answered` being non-empty sends the model
        back for another round, and that next round can itself contribute no
        text at all -- silently giving up rather than doing "the rest" or
        explaining why not. Confirmed in production: the promise was said two
        rounds before the empty round that actually triggered this check, so
        checking only that round's own (empty) slice missed it entirely."""
        world.script([*tool_use("remember", {"title": "t", "body": "b"}),
                      text(" I'll do the rest now.")])
        world.drive()
        rows = self._continuation(world)
        assert len(rows) == 1
        assert rows[0]["trigger"]["autoContinued"] == 1

    def test_a_tool_call_then_a_reply_that_genuinely_finishes_does_not_auto_continue(self, world):  # noqa: F811
        world.script([*tool_use("remember", {"title": "t", "body": "b"}),
                      text(" Saved.")])
        world.drive()
        assert self._continuation(world) == []

    def test_a_search_then_an_unfulfilled_close_in_a_later_round_still_auto_continues(self, world):  # noqa: F811
        """The shape that actually recurred in production: a tool call (a
        connector_search that found the tool) in round 1, then a later round
        that calls nothing and closes on a promise instead of following
        through with the action the search was for. Not narrowed by round
        count -- see _looks_unfulfilled's own docstring."""
        world.script([*tool_use("connector_search", {"query": "create a doc"})],
                     [text("Let me look up the available tools first.")])
        world.drive()
        rows = self._continuation(world)
        assert len(rows) == 1
        assert rows[0]["trigger"]["autoContinued"] == 1

    def test_stops_at_the_bound_like_the_ceiling_case_does(self, world):  # noqa: F811
        world.store.update(world.run["pk"], "META",
                           {"trigger": {"type": "user", "autoContinued": orch.MAX_AUTO_CONTINUES}})
        world.script([text("Let me do that now.")])
        world.drive()
        assert self._continuation(world) == []


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
