"""The whole loop, `_drive` end to end, with only AgentCore faked.

Everything the orchestrator does between "a run was queued" and "the run settled"
is real here: budget check, tool resolution, history, the stream parser, tool
handling, Auto Review, approvals, persistence, cancellation and redirect. What is
faked is the one thing that needs an AWS account -- the harness stream -- so the
model's side of each conversation is a script.

That is also the honest limit of this file: it proves the *orchestrator* does the
right thing with a given stream. It cannot prove the *service* emits that stream,
or how it accepts a continuation. That is decision D4, and it is not settled here.
"""

import json

import pytest

import handlers.api as api
import handlers.orchestrator as orch
from amazai import approvals, keys as K, memory, runs
from amazai.states import RunState

from tests.test_agents_api import api_table, call  # noqa: F401
from tests.fake_composio import WRITE
from tests.loop_world import POST, World


def text(t):
    return {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": t}}}


def tool_use(name, args, tool_use_id="tu-1", index=1):
    """The current AWS InvokeHarness shape (botocore 1.43.98).

    Start carries identity only; one or more `delta.toolUse.input` strings
    carry partial JSON. Keeping the shared end-to-end fixture provider-shaped
    prevents every loop test from passing against a stream AWS never sends.
    """
    encoded = json.dumps(args)
    at = max(1, len(encoded) // 2)
    return [
        {"contentBlockStart": {"contentBlockIndex": index, "start": {"toolUse": {
            "toolUseId": tool_use_id, "name": name}}}},
        {"contentBlockDelta": {"contentBlockIndex": index, "delta": {"toolUse": {
            "input": encoded[:at]}}}},
        {"contentBlockDelta": {"contentBlockIndex": index, "delta": {"toolUse": {
            "input": encoded[at:]}}}},
        {"contentBlockStop": {"contentBlockIndex": index}},
    ]


class FakeCore:
    """Stands in for `AgentCore`: each call to `invoke_stream` plays the next script."""

    def __init__(self, *scripts):
        self.scripts, self.calls = list(scripts), []

    def __call__(self, *a, **k):
        return self

    def invoke_stream(self, **kw):
        self.calls.append(kw)
        script = self.scripts.pop(0) if self.scripts else []
        yield from (script(kw) if callable(script) else script)


@pytest.fixture
def world(api_table, monkeypatch):
    w = World(api_table, monkeypatch)
    # D2 leaves `modelId` null until it is resolved for a real account; the loop
    # refuses to run without one, so give this agent one.
    w.store.update(K.agent_pk(w.store.owner_id, w.agent_id), "META",
                   {"model": {"modelId": "test-model", "tier": "balanced"},
                    "harnessArn": "arn:aws:bedrock-agentcore:us-west-2:1:harness/x"})
    w.fake = None

    def script(*scripts):
        w.fake = FakeCore(*scripts)
        monkeypatch.setattr(orch.agentcore, "AgentCore", w.fake)
        return w.fake
    w.script = script
    w.drive = lambda event=None: orch._drive(w.store, w.store.get(w.run["pk"], "META"),
                                             event or {"runId": w.run["runId"]})
    w.messages = lambda: w.store.query(K.thread_pk(w.store.owner_id, w.run["threadId"]), sk_prefix="MSG#")
    w.state = lambda: w.store.get(w.run["pk"], "META")["state"]
    return w


class TestRoomWakeStagger:
    """Production evidence: a five-member room wake fired five concurrent
    InvokeHarness calls at the one shared harness in the same second, and
    four came back ReadTimeoutError/502 (see WAKE_STAGGER_SECONDS in
    orchestrator.py). `wakeIndex` -- this run's position in the wake, set by
    `api._post_message` -- is what spreads the harness calls out instead."""

    def test_a_wake_index_sleeps_before_the_harness_is_called(self, world, monkeypatch):
        slept = []
        monkeypatch.setattr(orch.time, "sleep", slept.append)
        world.store.update(world.run["pk"], "META", {"trigger": {"type": "user", "wakeIndex": 2}})
        world.script([text("done")])
        world.drive()
        assert slept == [2 * orch.WAKE_STAGGER_SECONDS]

    def test_no_wake_index_never_sleeps(self, world, monkeypatch):
        slept = []
        monkeypatch.setattr(orch.time, "sleep", slept.append)
        world.script([text("done")])
        world.drive()
        assert slept == []

    def test_the_stagger_is_capped_so_a_large_room_does_not_wait_forever(self, world, monkeypatch):
        slept = []
        monkeypatch.setattr(orch.time, "sleep", slept.append)
        world.store.update(world.run["pk"], "META",
                           {"trigger": {"type": "user", "wakeIndex": 10}})
        world.script([text("done")])
        world.drive()
        assert slept == [orch.MAX_WAKE_STAGGER_SLOTS * orch.WAKE_STAGGER_SECONDS]


class TestDeliverFirstThenOffer:
    def test_the_result_is_said_and_the_offer_comes_after_it_as_a_card(self, world):
        # A Bot's own initiative, not a direct ask -- propose_routine only
        # creates a card here; see TestBotsCreateTheirOwnRoutines for the
        # owner-asked direct-create path.
        world.store.update(world.run["pk"], "META", {"trigger": {"type": "routine"}})
        world.script([
            text("Here is your plan for the week."),
            *tool_use("propose_routine", {"name": "Weekday planning", "schedule": "weekday-9",
                                          "prompt": "Draft the day."}),
        ])
        out = world.drive()
        assert out["state"] == RunState.COMPLETED.value
        reply = [m for m in world.messages() if m["role"] == "assistant"][-1]
        assert reply["text"] == "Here is your plan for the week."
        assert reply["cards"][0]["type"] == "routine" and reply["cards"][0]["preset"] == "weekday-9"
        assert [s["name"] for s in reply["steps"]] == ["propose_routine"]
        # Nothing was created: a card is a suggestion.
        assert call("GET", "/routines")[1]["routines"] == []


class TestRetryState:
    def test_a_second_transient_failure_retries_without_an_illegal_transition(self, world, monkeypatch):
        monkeypatch.setattr(orch, "_reinvoke", lambda *args, **kwargs: None)

        def unavailable(_):
            raise RuntimeError("service unavailable")

        world.script(unavailable)
        assert world.drive()["state"] == RunState.RETRYING.value

        # The next invocation starts in RETRYING. It must pass through
        # EXECUTING before it can be parked for another retry.
        world.script(unavailable)
        assert world.drive({"runId": world.run["runId"], "resume": True})["state"] == RunState.RETRYING.value
        saved = world.store.get(world.run["pk"], "META")
        assert saved["state"] == RunState.RETRYING.value and saved["attempt"] == 2

    def test_a_connect_offer_needs_no_pause_and_the_run_completes(self, world):
        world.script([text("I set up a workspace."),
                      *tool_use("request_connector", {"connectorId": "nonexistentapp", "why": "x"})])
        assert world.drive()["state"] == RunState.COMPLETED.value

    def test_a_bot_proposal_pauses_the_run_on_an_approval_that_names_its_rule(self, world):
        # A run the operator did not start (a schedule fired it): a Bot acting on its own
        # initiative. When the operator's message started it, the Bot is created at once.
        world.store.update(world.run["pk"], "META", {"trigger": {"type": "routine"}})
        world.script([text("Here is the plan."),
                      *tool_use("propose_agent", {"name": "Calendar", "role": "Keeps the calendar.",
                                                  "why": "A separate lane."})])
        assert world.drive()["state"] == RunState.AWAITING_APPROVAL.value
        card = call("GET", "/approvals")[1]["approvals"][0]
        assert card["action"] == "agent.create"
        assert card["policy"] == {"rule": "floor", "matched": "agent.create",
                                  "reason": "on the always-approve floor"}
        # The words were said before the pause, and are kept with the trail.
        said = [m for m in world.messages() if m["role"] == "assistant"][-1]
        assert said["text"] == "Here is the plan."
        assert said["steps"][0]["review"]["decision"] == "asked"


class TestAWriteIsHeldThenResumed:
    def test_a_slack_post_the_model_just_calls_pauses_the_run_and_never_reaches_slack(self, world):
        world.script([text("Posting it now."), *tool_use("connector_call", {"tool": WRITE, "arguments": POST})])
        assert world.drive()["state"] == RunState.AWAITING_APPROVAL.value
        assert world.executed == []
        step = [m for m in world.messages() if m["role"] == "assistant"][-1]["steps"][0]
        assert (step["name"], step["review"]["decision"], step["review"]["rule"]) == (WRITE, "asked", "default")

    def test_approving_resumes_and_the_same_call_now_runs_once(self, world):
        call_ = {"tool": WRITE, "arguments": POST}
        world.script([text("Posting it now."), *tool_use("connector_call", call_)],
                     [*tool_use("connector_call", call_, "tu-2"), text("Posted to #launch.")])
        world.drive()
        pending = call("GET", "/approvals")[1]["approvals"][0]
        status, body = call("POST", f"/approvals/{pending['runId']}/{pending['approvalId']}", {"approve": True})
        assert status == 200 and body["resumed"] is True

        # The decision arrives as the D4 fallback turn, appended after the history.
        world.drive({"runId": world.run["runId"], "resume": True,
                     "resumeNote": f'Your decision on "{WRITE}": approved.'})
        assert world.state() == RunState.COMPLETED.value
        assert len(world.executed) == 1, "the approved write ran zero or several times"
        final = [m for m in world.messages() if m["role"] == "assistant"][-1]
        assert final["text"] == "Posted to #launch."
        assert final["steps"][0]["review"]["rule"] == "approved"

    def test_the_resume_turn_is_the_last_message_the_model_is_sent(self, world):
        fake = world.script([text("ok")])
        world.drive({"runId": world.run["runId"], "resume": True, "resumeNote": "APPROVED-NOTE"})
        sent = fake.calls[0]["messages"]
        assert sent[-1] == {"role": "user", "content": [{"text": "APPROVED-NOTE"}]}

    def test_a_greeting_is_never_sent_to_the_model(self, world):
        from amazai import threads
        world.store.put({"pk": K.thread_pk(world.store.owner_id, world.run["threadId"]),
                         "sk": K.message_sk("2026-01-01T00:00:00Z", "a"),
                         "entity": "Message", "role": "assistant", "author": "Comms",
                         "text": "Hey — good to meet you.", "starter": True})
        world.store.put({"pk": K.thread_pk(world.store.owner_id, world.run["threadId"]),
                         "sk": K.message_sk("2026-01-01T00:00:01Z", "b"),
                         "entity": "Message", "role": "user", "author": "you", "text": "post it"})
        fake = world.script([text("ok")])
        world.drive()
        roles = [m["role"] for m in fake.calls[0]["messages"]]
        assert roles[0] == "user", f"the conversation opened on {roles[0]}"

    def test_a_history_line_is_never_sent_to_the_model(self, world):
        from amazai import threads
        world.store.put({"pk": K.thread_pk(world.store.owner_id, world.run["threadId"]),
                         "sk": K.message_sk("2026-01-01T00:00:01Z", "b"),
                         "entity": "Message", "role": "user", "author": "you", "text": "post it"})
        threads.event(world.store, world.run["threadId"], "Routine created: X", icon="clock")
        fake = world.script([text("ok")])
        world.drive()
        assert "Routine created" not in json.dumps(fake.calls[0]["messages"])


class TestStopAndRedirect:
    def test_a_redirect_that_arrives_mid_stream_stops_the_run_and_starts_the_next(self, world):
        def stream(kw):
            yield text("Working on A…")
            # The operator sends something new while the run is going.
            api._stop_run(world.store, world.store.get(world.run["pk"], "META"),
                          redirect_text="Actually do B")
            yield text(" still A")

        world.script(stream)
        out = world.drive()
        assert out["state"] == RunState.CANCELLED.value
        old = world.store.get(world.run["pk"], "META")
        assert old["state"] == RunState.CANCELLED.value and old["redirectedTo"]
        new = world.store.get(K.run_pk(old["redirectedTo"]), "META")
        assert new["goal"] == "Actually do B" and new["state"] == RunState.QUEUED.value
        # What was said before the stop is kept.
        assert any(m.get("text", "").startswith("Working on A") for m in world.messages())

    def test_a_plain_stop_settles_cancelled_and_starts_nothing(self, world):
        def stream(kw):
            yield text("Working…")
            api._stop_run(world.store, world.store.get(world.run["pk"], "META"))
            yield text(" more")

        world.script(stream)
        assert world.drive()["state"] == RunState.CANCELLED.value
        assert not world.store.get(world.run["pk"], "META").get("redirectedTo")

    def test_a_stop_that_lands_after_the_last_event_still_stops_the_run(self, world):
        def stream(kw):
            yield text("All done.")
            api._stop_run(world.store, world.store.get(world.run["pk"], "META"),
                          redirect_text="Do B")

        world.script(stream)
        assert world.drive()["state"] == RunState.CANCELLED.value, \
            "a stop after the final event settled the run as COMPLETED and lost the redirect"


class TestRequestNotes:
    def test_an_invoked_skill_is_named_in_the_system_prompt(self, world):
        world.store.update(world.run["pk"], "META", {"trigger": {"type": "user",
                           "skill": {"skillId": "weekly-plan", "name": "Weekly plan"}}})
        fake = world.script([text("ok")])
        world.drive()
        prompt = fake.calls[0]["system_prompt"]
        assert 'invoked the skill "Weekly plan"' in prompt

    def test_a_named_bot_in_a_direct_thread_nudges_toward_handoff_not_a_wake(self, world):
        world.store.update(world.run["pk"], "META", {"trigger": {"type": "user", "mentions": ["ops"]}})
        fake = world.script([text("ok")])
        world.drive()
        assert "use the handoff tool" in fake.calls[0]["system_prompt"]



class TestABotReadsBackWhatItSaved:
    """The loop half of tests/test_memory_reaches_the_prompt.py.

    `remember` wrote a row, the row was re-read on every later run, and the
    prompt builder dropped it because `validate` defaults kind to `note` and
    only foundational rows were injected. A Bot's own memory was a no-op it
    had no way to notice.
    """

    def test_a_fact_a_bot_saved_is_in_its_next_prompt(self, world):
        world.script([*tool_use("remember", {"scope": "agent",
                                            "title": "Deploys",
                                            "body": "staging is eu-west-1"}),
                      text("Noted.")])
        world.drive()

        # A second run, after the first has settled.
        second = runs.create(world.store, agent_id=world.agent_id,
                             thread_id=world.run["threadId"], goal="where is staging?")
        fake = world.script([text("eu-west-1.")])
        orch._drive(world.store, second, {"runId": second["runId"]})
        assert "staging is eu-west-1" in fake.calls[0]["system_prompt"], \
            "the Bot saved a fact and could not see it on the next run"

    def test_an_expiry_a_bot_set_is_stored_rather_than_discarded(self, world):
        world.script([*tool_use("remember", {"scope": "agent", "body": "sprint ends Tuesday",
                                            "expires_at": "2026-03-01T00:00:00Z"}),
                      text("Noted.")])
        world.drive()
        rows = [r for r in world.store.query(K.agent_pk(world.store.owner_id, world.agent_id), sk_prefix="MEM#")
                if r.get("entity") == "Memory"]
        assert rows and rows[0]["expiresAt"] == "2026-03-01T00:00:00Z"

    def test_a_log_a_bot_wrote_stays_out_of_its_next_prompt(self, world):
        world.script([*tool_use("remember", {"scope": "agent", "kind": "log",
                                             "body": "ran the nightly export"}),
                      text("Done.")])
        world.drive()
        second = runs.create(world.store, agent_id=world.agent_id,
                             thread_id=world.run["threadId"], goal="anything else?")
        fake = world.script([text("No.")])
        orch._drive(world.store, second, {"runId": second["runId"]})
        assert "ran the nightly export" not in fake.calls[0]["system_prompt"]

    def test_the_newest_facts_are_the_ones_read_back(self, world):
        # `mem_` ids are time-ordered, so an ascending limit returned the
        # *oldest* rows. A Bot past the read ceiling stopped seeing anything it
        # had recently learned.
        for i in range(6):
            world.store.put(memory.plan_write(
                {"body": f"fact-{i:03d}-end", "kind": "foundational"},
                K.agent_pk(world.store.owner_id, world.agent_id), scope="agent",
                source="agent", author=world.agent_id))

        original = orch.MAX_MEMORY
        orch.MAX_MEMORY = 3
        try:
            fake = world.script([text("ok")])
            world.drive()
            prompt = fake.calls[0]["system_prompt"]
        finally:
            orch.MAX_MEMORY = original

        assert "fact-005-end" in prompt, "the newest fact was not read back"
        assert "fact-000-end" not in prompt, "the oldest facts crowded out the newest"
def usage(input_tokens=0, output_tokens=0, **extra):
    """The trailing event that says what the call cost."""
    return {"metadata": {"usage": {"inputTokens": input_tokens,
                                   "outputTokens": output_tokens, **extra}}}


class TestSpendIsRecorded:
    """What a run cost has to reach the run row.

    Before this, `add_model` had no caller anywhere in the codebase: the usage
    event was parsed as UNKNOWN and dropped, so `totalUsd` was always 0.0,
    `spent_this_month` summed a column of zeros, and the hard-stop branch in
    `cost.check` could not be reached by any input. These assert the chain end
    to end -- stream event, run row, cost row, `GET /usage`.
    """

    def test_a_turn_records_what_it_spent_on_the_run(self, world):
        world.script([text("Done."), usage(input_tokens=1000, output_tokens=500)])
        assert world.drive()["state"] == RunState.COMPLETED.value
        assert world.store.get(world.run["pk"], "META")["costUsd"] > 0

    def test_tokens_land_on_the_cost_row(self, world):
        world.script([text("Done."), usage(input_tokens=1000, output_tokens=500)])
        world.drive()
        rows = [r for r in world.store.query(
            K.cost_pk(world.store.owner_id, world.agent_id,
                     world.store.get(world.run["pk"], "META")["createdAt"][:7]))
            if r.get("entity") == "Cost"]
        assert rows, "no COST# row was written for the run"
        assert rows[0]["inputTokens"] == 1000
        assert rows[0]["outputTokens"] == 500
        assert rows[0]["modelCalls"] == 1
        assert rows[0]["totalUsd"] > 0

    def test_the_usage_endpoint_reports_a_non_zero_total(self, world):
        world.script([text("Done."), usage(input_tokens=1000, output_tokens=500)])
        world.drive()
        status, body = call("GET", "/usage", qs={"agentId": world.agent_id})
        assert status == 200, body
        assert body["totalUsd"] > 0, "the Usage screen would still read $0.00"

    def test_a_turn_the_provider_reported_nothing_for_stays_free(self, world):
        # No usage event: no invented number. A missing report is not a charge.
        world.script([text("Done.")])
        world.drive()
        assert world.store.get(world.run["pk"], "META")["costUsd"] == 0.0

    def test_spend_accumulates_across_tool_rounds(self, world):
        world.script(
            [*tool_use("find_agents", {"query": "ops"}), usage(input_tokens=500, output_tokens=100)],
            [text("Found them."), usage(input_tokens=600, output_tokens=120)],
        )
        world.drive()
        row = world.store.get(world.run["pk"], "META")
        assert row["costUsd"] > 0
        cost_rows = [r for r in world.store.query(
                        K.cost_pk(world.store.owner_id, world.agent_id, row["createdAt"][:7]))
                     if r.get("entity") == "Cost"]
        assert cost_rows[0]["modelCalls"] == 2, "only one round's usage was counted"
        assert cost_rows[0]["inputTokens"] == 1100

    def test_runtime_seconds_are_recorded(self, world):
        world.script([text("Done."), usage(input_tokens=10, output_tokens=2)])
        world.drive()
        row = world.store.get(world.run["pk"], "META")
        cost_rows = [r for r in world.store.query(
                        K.cost_pk(world.store.owner_id, world.agent_id, row["createdAt"][:7]))
                     if r.get("entity") == "Cost"]
        assert cost_rows[0]["runtimeSeconds"] >= 0


class TestPerAgentBudgetsAreNoLongerEnforced:
    """Per-agent spend/tool-call/error ceilings were removed in favour of a
    single account-level credit gate (`billing.has_credit`, checked earlier
    in `_drive`, ahead of tool resolution -- not tested here). A Bot's own
    `budget` fields (perRunUsd, perMonthUsd, onCeiling, ...) are no longer
    read by `_drive` at all; this is a lock against that check quietly
    coming back, not a defense of a policy still in force."""

    def test_a_turn_that_reports_huge_spend_still_calls_every_scripted_round(self, world):
        fake = world.script(
            # The World fixture's Bot has perRunUsd = 1.00; this round alone
            # reports spend far past it, and used to stop the turn there.
            [*tool_use("find_agents", {"query": "ops"}), usage(output_tokens=200_000)],
            [text("still going")],
        )
        world.drive()
        assert len(fake.calls) == 2, "a huge reported spend stopped the run between rounds"
        reply = [m for m in world.messages() if m["role"] == "assistant"][-1]
        assert reply["text"] == "still going"

    def test_a_resumed_run_that_already_spent_a_lot_still_starts(self, world):
        world.store.update(world.run["pk"], "META", {"costUsd": 5.0})
        fake = world.script([text("carrying on")])
        out = world.drive()
        assert out["ok"] is True
        assert fake.calls, "a high recorded spend refused to start the run"



class TestARepeatedToolFailureNoLongerStopsTheTurn:
    """`toolErrorCount`/`consecutiveToolErrors` are still recorded on the run
    row (see the tests below), but nothing ceilings on them any more --
    per-agent error ceilings were removed along with the rest of the
    per-agent budget (see TestPerAgentBudgetsAreNoLongerEnforced). The round
    ceiling (MAX_TOOL_ROUNDS = 40) is the only thing left that would end a
    model repeatedly failing the same call, and this script is well under it.
    """

    def test_repeated_failures_do_not_end_the_turn_early(self, world):
        bad = {"scope": "nonsense", "body": "x"}     # `remember` refuses the scope
        fake = world.script(
            [*tool_use("remember", bad, "t1"), usage(output_tokens=10)],
            [*tool_use("remember", bad, "t2"), usage(output_tokens=10)],
            [*tool_use("remember", bad, "t3"), usage(output_tokens=10)],
            [*tool_use("remember", bad, "t4"), usage(output_tokens=10)],
            [text("still going")],
        )
        world.drive()
        assert len(fake.calls) == 5, \
            f"the turn ended after {len(fake.calls)} calls instead of running the whole script"

    def test_the_run_records_the_failures(self, world):
        bad = {"scope": "nonsense", "body": "x"}
        world.script(
            [*tool_use("remember", bad, "t1"), usage(output_tokens=10)],
            [*tool_use("remember", bad, "t2"), usage(output_tokens=10)],
            [*tool_use("remember", bad, "t3"), usage(output_tokens=10)],
            [text("done")],
        )
        world.drive()
        row = world.store.get(world.run["pk"], "META")
        assert row["toolErrorCount"] >= 3
        assert row["consecutiveToolErrors"] >= 3

    def test_a_success_clears_the_consecutive_count(self, world):
        bad = {"scope": "nonsense", "body": "x"}
        world.script(
            [*tool_use("remember", bad, "t1"), usage(output_tokens=10)],
            [*tool_use("remember", {"scope": "agent", "body": "a fact"}, "t2"),
             usage(output_tokens=10)],
            [*tool_use("remember", bad, "t3"), usage(output_tokens=10)],
            [text("done")],
        )
        world.drive()
        row = world.store.get(world.run["pk"], "META")
        # Two failures total, but they were not consecutive, so the run went on.
        assert row["toolErrorCount"] == 2
        assert row["consecutiveToolErrors"] == 1

    def test_a_turn_whose_tools_all_work_is_not_stopped(self, world):
        fake = world.script(
            [*tool_use("find_agents", {"query": "ops"}, "t1"), usage(output_tokens=10)],
            [*tool_use("find_agents", {"query": "eng"}, "t2"), usage(output_tokens=10)],
            [text("Found them."), usage(output_tokens=10)],
        )
        world.drive()
        assert len(fake.calls) == 3
        assert world.store.get(world.run["pk"], "META")["consecutiveToolErrors"] == 0



class TestToolInputStructureDiagnostics:
    def test_a_required_tool_with_no_input_logs_structure_not_argument_values(self, world, capsys):
        world.script([
            {"contentBlockStart": {"contentBlockIndex": 7, "start": {"toolUse": {
                "toolUseId": "missing-1", "name": "create_agent"}}}},
            {"contentBlockStop": {"contentBlockIndex": 7}},
        ])
        world.drive()
        line = next(line for line in capsys.readouterr().out.splitlines()
                    if "agentcore.tool_input_missing" in line)
        event = json.loads(line)
        assert event == {
            "event": "agentcore.tool_input_missing",
            "runId": world.run["runId"],
            "tool": "create_agent",
            "toolUseId": "missing-1",
            "blockIndex": 7,
            "requiredFieldCount": 3,
        }

    def test_a_valid_empty_input_for_an_optional_tool_is_not_reported_missing(self, world, capsys):
        world.script([
            {"contentBlockStart": {"contentBlockIndex": 2, "start": {"toolUse": {
                "toolUseId": "empty-ok", "name": "find_agents"}}}},
            {"contentBlockDelta": {"contentBlockIndex": 2, "delta": {"toolUse": {
                "input": "{}"}}}},
            {"contentBlockStop": {"contentBlockIndex": 2}},
            text("No query was needed."),
        ])
        world.drive()
        assert "agentcore.tool_input_missing" not in capsys.readouterr().out
