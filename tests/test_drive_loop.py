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
    return [
        {"contentBlockStart": {"contentBlockIndex": index, "start": {"toolUse": {
            "toolUseId": tool_use_id, "name": name}}}},
        {"contentBlockDelta": {"contentBlockIndex": index, "delta": {"toolUse": {
            "partial_json": json.dumps(args)}}}},
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
    w.store.update(K.agent_pk(w.agent_id), "META",
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
    w.messages = lambda: w.store.query(K.thread_pk(w.run["threadId"]), sk_prefix="MSG#")
    w.state = lambda: w.store.get(w.run["pk"], "META")["state"]
    return w


class TestDeliverFirstThenOffer:
    def test_the_result_is_said_and_the_offer_comes_after_it_as_a_card(self, world):
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
        world.store.put({"pk": K.thread_pk(world.run["threadId"]),
                         "sk": K.message_sk("2026-01-01T00:00:00Z", "a"),
                         "entity": "Message", "role": "assistant", "author": "Comms",
                         "text": "Hey — good to meet you.", "starter": True})
        world.store.put({"pk": K.thread_pk(world.run["threadId"]),
                         "sk": K.message_sk("2026-01-01T00:00:01Z", "b"),
                         "entity": "Message", "role": "user", "author": "you", "text": "post it"})
        fake = world.script([text("ok")])
        world.drive()
        roles = [m["role"] for m in fake.calls[0]["messages"]]
        assert roles[0] == "user", f"the conversation opened on {roles[0]}"

    def test_a_history_line_is_never_sent_to_the_model(self, world):
        from amazai import threads
        world.store.put({"pk": K.thread_pk(world.run["threadId"]),
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
        rows = [r for r in world.store.query(K.agent_pk(world.agent_id), sk_prefix="MEM#")
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
                K.agent_pk(world.agent_id), scope="agent",
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
