"""What the model is sent, and what a retry must never send it.

A person pasted a long message into Chief's chat. The first attempt failed part-way
(after some words and four tool calls), so the half-finished reply was saved -- and
the retry rebuilt the conversation from storage, which now *ended on that saved
assistant reply*. Current models refuse a conversation that ends on an assistant
turn ("does not support assistant message prefill"), so the retry failed; the error
read as a validation problem, which is re-planned, so it failed again, and the run
ended with "could not recover after 2 re-plans". Nothing the person did was wrong.

Each test here is one way that chain broke, and a way the same class of failure can
still arrive: a stopped-and-redirected run, a Bot woken with no user turn, and a
thread long enough that "the last 40" was really "the first 40".
"""

import json

import pytest

from amazai import agentcore, errors, keys as K

from tests.test_agents_api import api_table  # noqa: F401
from tests.test_drive_loop import text, world  # noqa: F401  (builds a drivable agent)

PREFILL = (
    "EventStreamError: An error occurred (runtimeClientError) when calling the InvokeHarness "
    "operation: An error occurred (ValidationException) when calling the ConverseStream "
    "operation: The model returned the following errors: This model does not support "
    "assistant message prefill. The conversation must end with a user message."
)


def put(world, ts, role, body, **extra):
    world.store.put({"pk": K.thread_pk(world.run["threadId"]), "sk": K.message_sk(ts, "x"),
                     "entity": "Message", "role": role,
                     "author": "you" if role == "user" else "Comms", "text": body, **extra})


def dies_after(words, error="throttling: too many requests"):
    """A stream that says something and then fails, as the incident's first attempt did."""
    def script(_kw):
        yield text(words)
        raise RuntimeError(error)
    return script


def last_turn(fake, call=-1):
    m = fake.calls[call]["messages"][-1]
    return m["role"], m["content"][0]["text"]


RETRY = {"resume": True, "note": "transient, backing off"}     # what `_reinvoke` sends


class TestARetryNeverEndsOnItsOwnHalfFinishedReply:
    def test_the_retry_is_sent_the_persons_message_last(self, world):  # noqa: F811
        put(world, "2026-01-01T00:00:01Z", "user", "Please make three Bots")
        fake = world.script(dies_after("Let me look at that"), [text("Done.")])

        world.drive()
        assert world.state() == "RETRYING"
        world.drive({"runId": world.run["runId"], **RETRY})

        assert last_turn(fake, 1) == ("user", "Please make three Bots")

    def test_its_half_finished_reply_stays_in_the_transcript_but_not_in_the_prompt(self, world):  # noqa: F811
        put(world, "2026-01-01T00:00:01Z", "user", "Please make three Bots")
        fake = world.script(dies_after("Let me look at that"), [text("Done.")])
        world.drive()
        world.drive({"runId": world.run["runId"], **RETRY})

        assert "Let me look at that" in [m["text"] for m in world.messages()]      # the person saw it
        assert "Let me look at that" not in json.dumps(fake.calls[1]["messages"])  # the model is not shown it as a prompt

    def test_the_error_that_started_the_retry_is_kept_on_the_run(self, world):  # noqa: F811
        put(world, "2026-01-01T00:00:01Z", "user", "hi")
        world.script(dies_after("...", error="ThrottlingException: slow down"))
        world.drive()
        run = world.store.get(world.run["pk"], "META")
        assert "ThrottlingException" in run["lastError"]

    def test_a_run_resuming_after_an_approval_keeps_what_it_said_before_it_paused(self, world):  # noqa: F811
        put(world, "2026-01-01T00:00:01Z", "user", "post the update")
        put(world, "2026-01-01T00:00:02Z", "assistant", "I need your OK to post this",
            runId=world.run["runId"])
        fake = world.script([text("Posted.")])

        world.drive({"runId": world.run["runId"], "resume": True, "resumeNote": "APPROVED"})

        sent = json.dumps(fake.calls[0]["messages"])
        assert "I need your OK to post this" in sent      # context it needs
        assert last_turn(fake) == ("user", "APPROVED")


class TestOtherWaysAConversationCouldEndOnTheAssistant:
    def test_a_redirected_run_ends_on_the_new_message_not_the_run_it_replaced(self, world):  # noqa: F811
        # Sent while the first run was streaming, so the new message is stored *before*
        # the stopped run's partial reply is.
        put(world, "2026-01-01T00:00:01Z", "user", "write the report")
        put(world, "2026-01-01T00:00:02Z", "user", "actually, make it a summary")
        put(world, "2026-01-01T00:00:03Z", "assistant", "Starting the report", runId="run_old")
        world.store.update(world.run["pk"], "META",
                           {"trigger": {"type": "user", "redirectOf": "run_old"}})
        fake = world.script([text("Here is the summary.")])

        world.drive()

        assert last_turn(fake) == ("user", "actually, make it a summary")
        assert "Starting the report" not in json.dumps(fake.calls[0]["messages"])

    def test_a_bot_woken_with_no_user_turn_is_asked_the_request_it_was_woken_for(self, world):  # noqa: F811
        put(world, "2026-01-01T00:00:01Z", "user", "hello")
        put(world, "2026-01-01T00:00:02Z", "assistant", "Hi!", runId="run_earlier")
        fake = world.script([text("On it.")])

        world.drive()            # the run's own goal is "post the update"

        assert last_turn(fake) == ("user", "post the update")

    def test_a_long_thread_sends_its_end_not_its_beginning(self, world):  # noqa: F811
        # 61 rows: `limit=40` in ascending order used to return the first 40 ever
        # written, so from the 41st message the model never saw the one it was
        # being asked to answer.
        for i in range(61):
            put(world, f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z",
                "user" if i % 2 == 0 else "assistant", f"m{i}")
        fake = world.script([text("ok")])

        world.drive()

        sent = fake.calls[0]["messages"]
        assert last_turn(fake) == ("user", "m60")
        assert len(sent) <= 40
        assert "m0" not in json.dumps(sent)               # the beginning is what falls away


class TestWhatIsRetried:
    def test_a_request_the_service_will_always_refuse_fails_at_once(self, world):  # noqa: F811
        put(world, "2026-01-01T00:00:01Z", "user", "hi")
        world.script(dies_after("...", error=PREFILL))

        result = world.drive()

        assert world.state() == "FAILED"
        assert result["ok"] is False
        assert world.store.get(world.run["pk"], "META").get("attempt", 0) == 0   # not retried

    def test_that_error_is_terminal_not_a_re_plan(self):
        c = errors.classify(PREFILL, attempt=0)
        assert c.cls is errors.ErrorClass.TERMINAL and not c.retryable

    def test_an_ordinary_validation_error_is_still_handed_back_to_the_model(self):
        c = errors.classify("ValidationException: the selector did not match", attempt=0)
        assert c.cls is errors.ErrorClass.NEEDS_REPLAN and c.retryable

    def test_a_throttle_is_still_retried_with_backoff(self):
        c = errors.classify("ThrottlingException: rate exceeded", attempt=0)
        assert c.cls is errors.ErrorClass.TRANSIENT and c.retryable


class TestTheBuildersOnTheirOwn:
    HISTORY = [
        {"role": "user", "text": "one"},
        {"role": "assistant", "text": "two", "runId": "run_a"},
        {"role": "assistant", "text": "three", "runId": "run_b"},
    ]

    def test_skipping_a_run_leaves_only_its_assistant_rows_out(self):
        out = agentcore.build_messages(self.HISTORY, skip_runs={"run_a"})
        assert [m["content"][0]["text"] for m in out] == ["one", "three"]

    def test_skipping_nothing_changes_nothing(self):
        assert len(agentcore.build_messages(self.HISTORY)) == 3

    def test_a_user_row_from_a_skipped_run_is_never_dropped(self):
        rows = [{"role": "user", "text": "mine", "runId": "run_a"}]
        assert len(agentcore.build_messages(rows, skip_runs={"run_a"})) == 1

    def test_ending_on_the_user_adds_nothing(self):
        msgs = [{"role": "user", "content": [{"text": "hi"}]}]
        assert agentcore.end_on_user(msgs, "goal") is msgs

    def test_ending_on_the_assistant_adds_the_goal_as_a_user_turn(self):
        msgs = [{"role": "assistant", "content": [{"text": "hi"}]}]
        out = agentcore.end_on_user(msgs, "  do the thing  ")
        assert out[-1] == {"role": "user", "content": [{"text": "do the thing"}]}
        assert msgs[-1]["role"] == "assistant"            # the input was not mutated

    def test_with_no_goal_it_says_nothing_on_anyones_behalf(self):
        msgs = [{"role": "assistant", "content": [{"text": "hi"}]}]
        assert agentcore.end_on_user(msgs, "") is msgs
        assert agentcore.end_on_user([], "") == []

    def test_a_bot_never_spoken_to_is_asked_its_goal(self):
        # A new Bot's greeting is not sent, so a Bot woken to do a first job has no
        # history at all: the goal is its whole conversation.
        assert agentcore.end_on_user([], "do the thing") == [
            {"role": "user", "content": [{"text": "do the thing"}]}]
