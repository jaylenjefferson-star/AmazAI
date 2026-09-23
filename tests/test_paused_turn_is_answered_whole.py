"""A turn that stops for a decision is answered whole when it resumes.

The model can ask for several things at once. One of them needing the operator's
decision used to mean the rest were abandoned: the loop left the stream at the
approval, threw away the results it already had, and on resume replayed only the
approval's own pair. The service, still holding the ids it handed out for that
turn, rejected the next call outright:

    EventStreamError (runtimeClientError) calling InvokeHarness:
    Inline function result is missing toolUseId 'tooluse_...'

Nothing the Bot or the operator did caused that -- it was bookkeeping. So what is
proved here is completeness: every id the model produced in the paused turn comes
back with a result, in the order it asked, with the decision in its own place, and
the calls that came after the pause answered but *not run*.
"""

import json

import pytest

import handlers.orchestrator as orch
from amazai import continuation, keys as K
from amazai.states import RunState

from tests.fake_composio import WRITE
from tests.loop_world import POST
from tests.test_agents_api import api_table, call  # noqa: F401
from tests.test_drive_loop import text, tool_use, world  # noqa: F401

REMEMBER = {"title": "Pacific time", "body": "Schedule in America/Los_Angeles."}


@pytest.fixture(autouse=True)
def native(monkeypatch):
    """The shape that is deployed: `AMAZAI_CONTINUATION=tool_result`."""
    monkeypatch.setenv("AMAZAI_CONTINUATION", "tool_result")


def paused_turn(world):  # noqa: F811
    rows = world.store.query(world.run["pk"], sk_prefix="PAUSE#")
    return rows[0] if rows else None


def decide(world, approve=True):  # noqa: F811
    """Approve through the real endpoint and return the resume event it would send."""
    pending = call("GET", "/approvals")[1]["approvals"][0]
    status, _ = call("POST", f"/approvals/{pending['runId']}/{pending['approvalId']}",
                     {"approve": approve})
    assert status == 200
    decided = world.store.get(world.run["pk"], K.approval_sk(pending["approvalId"]))
    verb = "approved" if approve else "denied"
    return {"runId": world.run["runId"], "resume": True,
            "resumeNote": f'Your decision on "{decided["action"]}": {verb}.',
            "resumeApproval": decided}


def a_turn_that_pauses_in_the_middle(world):  # noqa: F811
    """Three calls in one turn: one that runs, one that needs a decision, one after.

    The second script is what the model does once the decision reaches it: an
    approval authorises the call, it does not perform it, so the write only
    happens because the model asks for it again.
    """
    return world.script(
        [text("Noting the timezone, then posting."),
         *tool_use("remember", REMEMBER, "tu-1", 1),
         *tool_use("connector_call", {"tool": WRITE, "arguments": POST}, "tu-2", 2),
         *tool_use("remember", {"title": "After", "body": "Ran after the pause."}, "tu-3", 3)],
        [*tool_use("connector_call", {"tool": WRITE, "arguments": POST}, "tu-4", 1),
         text("Posted, and noted.")])


class TestTheWholeTurnIsRecordedWhenItPauses:
    def test_the_run_still_pauses_on_the_decision(self, world):  # noqa: F811
        a_turn_that_pauses_in_the_middle(world)
        assert world.drive()["state"] == RunState.AWAITING_APPROVAL.value
        assert world.executed == [], "the held write reached Slack"

    def test_every_call_in_the_turn_is_recorded_in_the_order_it_was_asked(self, world):  # noqa: F811
        a_turn_that_pauses_in_the_middle(world)
        world.drive()
        calls = paused_turn(world)["calls"]
        assert [c["toolUseId"] for c in calls] == ["tu-1", "tu-2", "tu-3"]
        assert [c["name"] for c in calls] == ["remember", "connector_call", "remember"]
        assert calls[1]["approval"] is True                      # the decision's own place
        assert "approval" not in calls[0] and "approval" not in calls[2]

    def test_what_already_ran_keeps_its_result(self, world):  # noqa: F811
        a_turn_that_pauses_in_the_middle(world)
        world.drive()
        first = paused_turn(world)["calls"][0]
        assert json.loads(first["text"])["saved"] is True and first["error"] is False

    def test_a_call_after_the_pause_is_answered_but_not_run(self, world):  # noqa: F811
        a_turn_that_pauses_in_the_middle(world)
        world.drive()
        after = paused_turn(world)["calls"][2]
        assert after["error"] is True
        assert "not run" in after["text"]
        # The memory it asked for after the pause was never written.
        saved = [m["title"] for m in world.store.query(K.agent_pk(world.store.owner_id, world.agent_id), sk_prefix="MEM#")]
        assert "After" not in saved and "Pacific time" in saved

    def test_the_trail_says_the_held_call_is_waiting_on_the_operator(self, world):  # noqa: F811
        a_turn_that_pauses_in_the_middle(world)
        world.drive()
        steps = [m for m in world.messages() if m["role"] == "assistant"][-1]["steps"]
        held = steps[-1]
        assert held["name"] == "remember" and held["summary"] == "held until you decide"
        assert (held["review"]["decision"], held["review"]["rule"]) == ("asked", "paused")

    def test_a_turn_whose_only_call_is_the_decision_records_just_that(self, world):  # noqa: F811
        world.script([text("Posting it."),
                      *tool_use("connector_call", {"tool": WRITE, "arguments": POST})])
        world.drive()
        assert [c["toolUseId"] for c in paused_turn(world)["calls"]] == ["tu-1"]


class TestTheResumedTurnAnswersEveryId:
    def test_every_id_the_model_produced_comes_back_with_a_result(self, world):  # noqa: F811
        fake = a_turn_that_pauses_in_the_middle(world)
        world.drive()
        world.drive(decide(world))

        asked, answered = fake.calls[1]["messages"][-2:]
        assert [b["toolUse"]["toolUseId"] for b in asked["content"]] == ["tu-1", "tu-2", "tu-3"]
        assert [b["toolResult"]["toolUseId"] for b in answered["content"]] == ["tu-1", "tu-2", "tu-3"]
        assert asked["role"] == "assistant" and answered["role"] == "user"

    def test_the_decision_arrives_as_the_result_of_the_call_that_asked_for_it(self, world):  # noqa: F811
        fake = a_turn_that_pauses_in_the_middle(world)
        world.drive()
        world.drive(decide(world))

        held = fake.calls[1]["messages"][-1]["content"][1]["toolResult"]
        assert held["toolUseId"] == "tu-2" and held["status"] == "success"
        assert held["content"][0]["text"] == f'Your decision on "{WRITE}": approved.'

    def test_a_denial_comes_back_as_an_error_on_that_same_call(self, world):  # noqa: F811
        fake = a_turn_that_pauses_in_the_middle(world)
        world.drive()
        world.drive(decide(world, approve=False))
        held = fake.calls[1]["messages"][-1]["content"][1]["toolResult"]
        assert held["toolUseId"] == "tu-2" and held["status"] == "error"

    def test_the_results_that_already_ran_are_handed_back_unchanged(self, world):  # noqa: F811
        fake = a_turn_that_pauses_in_the_middle(world)
        world.drive()
        world.drive(decide(world))
        first = fake.calls[1]["messages"][-1]["content"][0]["toolResult"]
        assert first["status"] == "success" and json.loads(first["content"][0]["text"])["saved"] is True

    def test_the_run_gets_to_finish(self, world):  # noqa: F811
        a_turn_that_pauses_in_the_middle(world)
        world.drive()
        world.drive(decide(world))
        assert world.state() == RunState.COMPLETED.value
        assert len(world.executed) == 1, "the approved write ran zero or several times"

    def test_the_conversation_still_ends_on_the_operators_side(self, world):  # noqa: F811
        fake = a_turn_that_pauses_in_the_middle(world)
        world.drive()
        world.drive(decide(world))
        assert fake.calls[1]["messages"][-1]["role"] == "user"

    def test_a_pause_recorded_before_this_existed_still_resumes(self, world):  # noqa: F811
        """No `PAUSE#` row: the approval's own pair is still a valid answer."""
        fake = a_turn_that_pauses_in_the_middle(world)
        world.drive()
        row = paused_turn(world)
        world.store.delete(row["pk"], row["sk"])

        world.drive(decide(world))

        answered = fake.calls[1]["messages"][-1]["content"]
        assert [b["toolResult"]["toolUseId"] for b in answered] == ["tu-2"]
        assert world.state() == RunState.COMPLETED.value


class TestATurnThatPausesLateKeepsItsEarlierRounds:
    """A turn is several rounds, and only the last one is in `answered` when it
    pauses -- the rest live in `carried`, which is memory and dies with the
    Lambda. Losing them leaves the model resuming with no record of calls it
    already made, free to make them again."""

    def a_turn_that_pauses_in_round_three(self, world):  # noqa: F811
        return world.script(
            [text("First."), *tool_use("remember", {"title": "one", "body": "b"}, "tu-1", 1)],
            [text("Second."), *tool_use("remember", {"title": "two", "body": "b"}, "tu-2", 1)],
            [text("Now posting."),
             *tool_use("connector_call", {"tool": WRITE, "arguments": POST}, "tu-3", 1)],
            [text("Posted.")])

    def test_the_earlier_rounds_are_recorded_with_the_pause(self, world):  # noqa: F811
        self.a_turn_that_pauses_in_round_three(world)
        world.drive()
        row = paused_turn(world)
        assert [c["toolUseId"] for c in row["calls"]] == ["tu-3"]
        carried_ids = [b["toolUse"]["toolUseId"] for turn in row["carried"]
                       for b in turn["content"] if "toolUse" in b]
        assert carried_ids == ["tu-1", "tu-2"], "rounds 1 and 2 were lost at the pause"

    def test_the_resumed_turn_is_the_whole_turn(self, world):  # noqa: F811
        fake = self.a_turn_that_pauses_in_round_three(world)
        world.drive()
        world.drive(decide(world))

        sent = fake.calls[3]["messages"]
        used = [b["toolUse"]["toolUseId"] for m in sent for b in m.get("content", [])
                if "toolUse" in b]
        got = [b["toolResult"]["toolUseId"] for m in sent for b in m.get("content", [])
               if "toolResult" in b]
        assert used == ["tu-1", "tu-2", "tu-3"] and got == used

    def test_what_it_said_is_not_replayed_twice(self, world):  # noqa: F811
        """The prose of those rounds is already one persisted assistant message,
        which `build_messages` replays from storage."""
        fake = self.a_turn_that_pauses_in_round_three(world)
        world.drive()
        world.drive(decide(world))
        texts = [b["text"] for m in fake.calls[3]["messages"]
                 for b in m.get("content", []) if "text" in b]
        assert sum(1 for t in texts if "First." in t) == 1


class TestTheFallbackShapeIsUntouched:
    def test_a_resume_note_run_still_gets_one_plain_turn(self, world, monkeypatch):  # noqa: F811
        monkeypatch.setenv("AMAZAI_CONTINUATION", "resume_note")
        fake = a_turn_that_pauses_in_the_middle(world)
        world.drive()
        event = decide(world)
        world.drive(event)
        sent = fake.calls[1]["messages"]
        assert sent[-1] == {"role": "user", "content": [{"text": event["resumeNote"]}]}
        assert "toolResult" not in json.dumps(sent)


class TestACallWithNoIdCannotBreakTheRest:
    def test_an_id_less_call_is_left_out_rather_than_sent_empty(self):
        turns = continuation.tool_round_messages("", [
            {"toolUseId": "", "name": "remember", "input": {}, "result": {"ok": True}},
            {"toolUseId": "tu-2", "name": "remember", "input": {}, "result": {"ok": True}}])
        assert [b["toolUse"]["toolUseId"] for b in turns[0]["content"]] == ["tu-2"]
        assert [b["toolResult"]["toolUseId"] for b in turns[1]["content"]] == ["tu-2"]

    def test_a_round_with_nothing_answerable_ends_the_turn_instead_of_asking_again(self, world):  # noqa: F811
        """An id-less call cannot be answered, so asking the model again would only
        repeat the same question at the cost of another model call."""
        no_id = [
            {"contentBlockStart": {"contentBlockIndex": 1, "start": {"toolUse": {"name": "remember"}}}},
            {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {
                "input": json.dumps(REMEMBER)}}}},
            {"contentBlockStop": {"contentBlockIndex": 1}},
        ]
        fake = world.script([text("Noting."), *no_id], [text("never asked for")])
        world.drive()
        assert len(fake.calls) == 1 and world.state() == RunState.COMPLETED.value


class TestALongTurnDoesNotReUploadItselfEveryRound:
    def turns(self, rounds, size):
        out = []
        for i in range(rounds):
            out += continuation.tool_round_messages(
                "", [{"toolUseId": f"tu-{i}", "name": "connector_call", "input": {},
                      "result": {"data": "x" * size}}])
        return out

    def test_a_short_turn_is_sent_exactly_as_it_was(self):
        turns = self.turns(2, 100)
        assert continuation.compact(turns) == turns

    def test_the_oldest_results_are_shortened_once_the_payload_is_large(self):
        turns = self.turns(12, 20_000)
        out = continuation.compact(turns)
        texts = [b["toolResult"]["content"][0]["text"]
                 for row in out for b in row["content"] if "toolResult" in b]
        assert len(texts[0]) < 2_000 and "shortened" in texts[0]
        assert len(texts[-1]) > 20_000, "the newest result is what the model is acting on"
        assert sum(len(t) for t in texts) <= continuation.MAX_CARRIED_CHARS

    def test_no_call_and_no_id_is_ever_dropped(self):
        turns = self.turns(12, 20_000)
        out = continuation.compact(turns)
        ids = lambda rows, key: [b[key]["toolUseId"] for r in rows for b in r["content"] if key in b]  # noqa: E731
        assert ids(out, "toolUse") == ids(turns, "toolUse")
        assert ids(out, "toolResult") == ids(turns, "toolResult")

    def test_the_caller_s_own_list_is_not_rewritten(self):
        turns = self.turns(12, 20_000)
        before = json.dumps(turns)
        continuation.compact(turns)
        assert json.dumps(turns) == before


class TestTheRecordedTurnStaysSmallWithoutLosingACall:
    """The record has to be bounded *and* complete, and those pull against each
    other. What is made lossy is the payload -- a result, a set of arguments --
    never the list of ids, because an id with no result is the whole bug."""

    def test_a_held_result_is_truncated_for_storage(self):
        calls = [{"toolUseId": "tu-1", "name": "connector_call", "input": {},
                  "result": {"data": "x" * 50_000}, "error": False}]
        kept = continuation.paused_turn_calls(calls)
        assert len(kept[0]["text"]) == continuation.PAUSED_RESULT_CHARS

    def test_oversized_arguments_are_shortened_too(self):
        kept = continuation.paused_turn_calls([
            {"toolUseId": "tu-1", "name": "remember",
             "input": {"body": "x" * 50_000}, "result": {"ok": True}}])
        assert "_shortened" in kept[0]["input"] and "body" not in kept[0]["input"]
        assert kept[0]["toolUseId"] == "tu-1", "the id survives whatever else does not"

    def test_no_call_is_ever_dropped_by_count(self, world):  # noqa: F811
        """A positional cut here would re-create the exact production error on the
        very path that exists to prevent it: the ids past the cut come back with
        no result."""
        blocks, ids = [], ["tu-apv"]
        for i in range(30):
            ids.append(f"tu-{i}")
            blocks += tool_use("remember", {"title": f"t{i}", "body": "b"}, f"tu-{i}", i + 1)
        world.script([*tool_use("connector_call", {"tool": WRITE, "arguments": POST}, "tu-apv", 0),
                      *blocks])
        world.drive()
        assert [c["toolUseId"] for c in paused_turn(world)["calls"]] == ids

    def test_and_all_of_them_are_answered_on_resume(self, world):  # noqa: F811
        blocks = []
        for i in range(30):
            blocks += tool_use("remember", {"title": f"t{i}", "body": "b"}, f"tu-{i}", i + 1)
        fake = world.script(
            [*tool_use("connector_call", {"tool": WRITE, "arguments": POST}, "tu-apv", 0), *blocks],
            [text("done")])
        world.drive()
        world.drive(decide(world))
        asked, answered = fake.calls[1]["messages"][-2:]
        assert ([b["toolUse"]["toolUseId"] for b in asked["content"]]
                == [b["toolResult"]["toolUseId"] for b in answered["content"]])
        assert len(answered["content"]) == 31


class TestAGateThatFiresBeforeTheHarnessRotatesAnAbandonedResume:
    """`_paused_turn` -- the code that actually sends a resume's answer back
    to the harness -- runs well into `_drive`, after the kill switch, the
    credit check, the agent-state check, the missing-model-id check and the
    start-of-run Budget check. Any one of those refusing a *resuming*
    invocation means the harness is never reached this turn, so the answer
    that resume was carrying never gets sent -- leaving the session in
    exactly the state this whole file exists to prevent, just reached from
    an earlier point in the function than a mid-turn stream error or ceiling.

    This is a live bug that shipped and broke a real account's Bot in
    production (the account ran out of credits while a run was paused on an
    approval; resuming hit the new credit gate, which failed the run without
    rotating the session it was abandoning) -- caught, diagnosed from
    CloudWatch and DynamoDB, and fixed with `_rotate_if_abandoning_a_resume`.
    These are the regression guard.
    """

    def test_the_kill_switch_rotates_an_abandoned_resume(self, world):  # noqa: F811
        from amazai import agents as A, govern, keys as K
        a_turn_that_pauses_in_the_middle(world)
        world.drive()
        resume = decide(world)

        world.store.put(govern.killswitch_row(
            world.store.owner_id, frozen=True,
            actor=A.Actor(user_id=world.store.owner_id, org_id=world.store.owner_id),
            reason="incident"))
        out = orch._drive(world.store, world.store.get(world.run["pk"], "META"), resume)

        assert out == {"ok": False, "reason": "org frozen"}
        from amazai import runs
        assert runs.session_epoch(world.store, world.agent_id, f"dm-{world.agent_id}") == 1
        # The rotated epoch was also written onto this run's own row, the
        # same way a mid-turn _mark_dirty does -- a same-run retry must not
        # land back on the session it just rotated away from.
        fresh = K.bot_session_id(world.store.owner_id, world.agent_id,
                                 f"dm-{world.agent_id}", epoch=1)
        assert world.store.get(world.run["pk"], "META")["sessionId"] == fresh

    def test_running_out_of_credit_rotates_an_abandoned_resume(self, world, monkeypatch):  # noqa: F811
        from amazai import billing, runs
        a_turn_that_pauses_in_the_middle(world)
        world.drive()
        resume = decide(world)

        monkeypatch.setattr(billing, "has_credit", lambda store: False)
        out = orch._drive(world.store, world.store.get(world.run["pk"], "META"), resume)

        assert out == {"ok": False, "reason": "out of credits"}
        assert runs.session_epoch(world.store, world.agent_id, f"dm-{world.agent_id}") == 1

    def test_a_plain_new_run_hitting_the_kill_switch_has_nothing_to_rotate(self, world):  # noqa: F811
        """The other half of the fix: a run that never paused has no
        session debt, so a gate firing on it must stay a no-op rotation-wise."""
        from amazai import agents as A, govern, runs
        world.script([text("won't get here")])
        world.store.put(govern.killswitch_row(
            world.store.owner_id, frozen=True,
            actor=A.Actor(user_id=world.store.owner_id, org_id=world.store.owner_id),
            reason="incident"))

        out = orch._drive(world.store, world.store.get(world.run["pk"], "META"),
                          {"runId": world.run["runId"]})

        assert out == {"ok": False, "reason": "org frozen"}
        assert runs.session_epoch(world.store, world.agent_id, f"dm-{world.agent_id}") == 0

    def test_a_plain_retry_hitting_the_kill_switch_has_nothing_to_rotate(self, world):  # noqa: F811
        """`resume: True` alone (no resumeNote) is orchestrator._reinvoke's
        plain retry, not an approval resume -- continuation.is_approval_resume
        says so explicitly. Must not be mistaken for one here either."""
        from amazai import agents as A, govern, runs
        world.script([text("won't get here")])
        world.store.put(govern.killswitch_row(
            world.store.owner_id, frozen=True,
            actor=A.Actor(user_id=world.store.owner_id, org_id=world.store.owner_id),
            reason="incident"))

        out = orch._drive(world.store, world.store.get(world.run["pk"], "META"),
                          {"runId": world.run["runId"], "resume": True})

        assert out == {"ok": False, "reason": "org frozen"}
        assert runs.session_epoch(world.store, world.agent_id, f"dm-{world.agent_id}") == 0
