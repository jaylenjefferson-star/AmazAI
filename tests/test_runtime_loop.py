"""The agent loop, driven through the real `_handle_tool`.

These tests exist for one claim, and it is the one the whole product rests on:
**enforcement is code, never prompt.** A connector write on the always-approve
floor must stop for a person whether or not the model remembered to ask first;
an approval must be spent once and only on the arguments it was given; and every
step must carry the verdict and the rule behind it.

Only the network hop to Composio is faked. The store, the policy engine, the
router, the approvals and the orchestrator's own handler are the real ones.
"""

import pytest

import handlers.api as api
import handlers.orchestrator as orch
from amazai import agentcore, approvals, connectors as C, keys as K, policy, review, router, runs, threads
from amazai.cost import RunCost
from amazai.evidence import EvidenceWriter
from amazai.states import RunState
from amazai.store import Store

from tests.fake_composio import DELETE, READ, UNLABELLED, WRITE, FakeTransport, wire
from tests.loop_world import POST, SLACK, RecPush, World, tool_call, world  # noqa: F401
from tests.test_agents_api import api_table, call  # noqa: F401

class TestAConnectorWriteStopsForAPersonInCode:
    """`connector_call` is the only way a model reaches an app, and the gate is
    in the handler, not in the prompt: a Bot that never calls `request_approval`
    and just calls a write is stopped all the same."""

    def test_a_read_runs_and_says_why(self, world):
        result = world.use(READ, {"channel": "C123"})
        assert result["pause"] is False
        assert len(world.executed) == 1
        assert world.last["review"]["decision"] == review.ALLOWED
        assert world.last["review"]["rule"] == "read"

    def test_a_write_the_model_never_asked_about_does_not_run(self, world):
        result = world.use(WRITE, POST)
        assert result["pause"] is True
        assert world.executed == [], "the write reached Slack without approval"
        assert world.last["review"]["decision"] == review.ASKED

    def test_the_approval_names_the_rule_that_stopped_it(self, world):
        approval = world.use(WRITE, POST)["approval"]
        card = approvals.to_card(approval)
        assert card["action"] == WRITE
        assert card["policy"]["rule"] == "default"
        assert card["policy"]["matched"] == "write"
        assert card["arguments"] == POST

    def test_the_paused_call_is_recorded_so_the_run_can_resume_it_natively(self, world):
        approval = world.use(WRITE, POST)["approval"]
        assert approval["toolName"] == "connector_call"
        assert approval["toolInput"] == {"tool": WRITE, "arguments": POST}

    def test_once_approved_the_same_call_runs(self, world):
        approval = world.use(WRITE, POST)["approval"]
        world.approve(approval)
        result = world.use(WRITE, POST, tool_use_id="tu-2")
        assert result["pause"] is False
        assert len(world.executed) == 1
        assert world.last["review"]["decision"] == review.ALLOWED
        assert world.last["review"]["rule"] == "approved"
        assert world.last["review"]["matched"] == approval["approvalId"]

    def test_an_approval_is_spent_once(self, world):
        approval = world.use(WRITE, POST)["approval"]
        world.approve(approval)
        world.use(WRITE, POST, tool_use_id="tu-2")
        again = world.use(WRITE, POST, tool_use_id="tu-3")
        assert again["pause"] is True, "an approval authorised a second post"
        assert len(world.executed) == 1

    def test_an_approval_does_not_cover_different_arguments(self, world):
        approval = world.use(WRITE, POST)["approval"]
        world.approve(approval)
        other = world.use(WRITE, {"channel": "C123", "text": "Something else."}, tool_use_id="tu-2")
        assert other["pause"] is True
        assert world.executed == []

    def test_a_denied_approval_does_not_unlock_the_call(self, world):
        approval = world.use(WRITE, POST)["approval"]
        approvals.decide(world.store, world.run["pk"], approval["approvalId"], approve=False)
        assert world.use(WRITE, POST, tool_use_id="tu-2")["pause"] is True
        assert world.executed == []

    def test_an_unlabelled_tool_is_treated_as_a_write(self, world):
        # Composio tagged nothing on it. Guessing "read" here would be the leak.
        assert world.use(UNLABELLED, {})["pause"] is True
        assert world.executed == []

    def test_an_owner_can_pre_approve_one_specific_write_for_one_bot(self, world):
        world.agent = {**world.agent, "preapproved": [WRITE]}
        result = world.use(WRITE, POST)
        assert result["pause"] is False and len(world.executed) == 1
        assert world.last["review"]["rule"] == "preapproved"

    def test_dont_ask_again_saved_through_the_api_takes_effect_on_the_next_call(self, world):
        # What the approval card's checkbox does: the owner's own audited PATCH.
        status, _ = call("PATCH", f"/agents/{world.agent_id}", {"preapproved": [WRITE]})
        assert status == 200
        world.agent = world.store.get(K.agent_pk(world.agent_id), "META")
        assert world.use(WRITE, POST)["pause"] is False
        assert world.last["review"]["rule"] == "preapproved"
        # It named one action: a different write on the same app still asks.
        assert world.use("SLACK_A_TOOL_WITH_NO_TAGS", {}, tool_use_id="tu-2")["pause"] is True

    def test_a_destructive_tool_stays_gated_even_if_it_is_saved_as_pre_approved(self, world):
        call("PATCH", f"/agents/{world.agent_id}", {"preapproved": [WRITE, DELETE]})
        world.agent = world.store.get(K.agent_pk(world.agent_id), "META")
        assert world.use(DELETE, {"channel": "C1", "ts": "1"})["pause"] is True
        assert world.executed == []

    def test_a_destructive_tool_can_never_be_pre_approved(self, world):
        world.agent = {**world.agent, "preapproved": [DELETE]}
        result = world.use(DELETE, {"channel": "C1", "ts": "1"})
        assert result["pause"] is True, "a pre-approved rule lowered a destructive tool's gate"
        assert world.executed == []
        assert result["approval"]["policy"]["rule"] == "capability"

    def test_a_bot_skipping_request_approval_is_stopped_all_the_same(self, world):
        # The model is never trusted to have asked. There is no path to Slack
        # that does not go through `connector_call`.
        world.use(WRITE, POST)
        assert world.executed == []


class TestWhatABotMayReach:
    def test_search_shows_only_what_the_grant_allows(self, world):
        found = {t["tool"]: t for t in world.search("slack")["tools"]}
        assert READ in found and WRITE in found
        assert found[READ]["effect"] == "reads only" and found[WRITE]["effect"] == "changes data"
        assert found[DELETE]["effect"] == "removes data"
        assert set(found[WRITE]["inputs"]["required"]) == {"channel", "text"}

    def test_a_read_only_bot_is_never_shown_a_tool_that_writes(self, world):
        world.store.put(C.grant_row(world.agent_id, SLACK, actor_user_id="owner-a",
                                    capability=C.Capability.READ))
        found = {t["tool"] for t in world.search("slack")["tools"]}
        assert found == {READ}

    def test_and_cannot_call_one_it_was_not_shown(self, world):
        world.store.put(C.grant_row(world.agent_id, SLACK, actor_user_id="owner-a",
                                    capability=C.Capability.READ))
        out = world.use(WRITE, POST)
        assert out["toolResult"]["error"].endswith("is read-only") or "read-only" in out["toolResult"]["error"]
        assert world.executed == []
        assert world.last["review"]["decision"] == review.DENIED
        assert world.last["review"]["rule"] == "no_grant"

    def test_an_app_the_bot_does_not_hold_is_not_searched_and_not_callable(self, world):
        assert world.search("mail", app="gmail")["tools"] == []
        out = world.use("GMAIL_FETCH_EMAILS", {})
        assert "gmail is not connected" in out["toolResult"]["error"]
        assert world.executed == []

    def test_with_nothing_connected_the_bot_is_told_what_to_do_instead(self, api_table, monkeypatch):
        w = World(api_table, monkeypatch, connected=set(), install=False)
        out = w.search("send an email")
        assert out["tools"] == [] and "request_connector" in out["note"]

    def test_a_tool_that_does_not_exist_is_an_error_the_bot_can_read(self, world):
        out = world.use("SLACK_MADE_UP_TOOL", {})
        assert "could not look up" in out["toolResult"]["error"]
        assert world.executed == []

    def test_a_slug_that_could_rewrite_the_url_is_refused_before_any_request(self, world):
        before = len(world.transport.requests)
        out = world.use("../connected_accounts", {})
        assert "toolResult" in out and world.executed == []
        assert len(world.transport.requests) == before

    def test_a_revoke_between_two_calls_stops_the_second(self, world):
        assert world.use(READ, {"channel": "C1"})["pause"] is False
        call("DELETE", f"/connectors/{SLACK}")
        out = world.use(READ, {"channel": "C1"}, tool_use_id="tu-2")
        assert "not connected" in out["toolResult"]["error"]
        assert len(world.executed) == 1

    def test_a_failing_call_reports_back_and_does_not_pretend(self, api_table, monkeypatch):
        w = World(api_table, monkeypatch)
        w.transport.fail[READ] = "channel_not_found"
        out = w.use(READ, {"channel": "nope"})
        assert "channel_not_found" in out["toolResult"]["error"]


class TestEveryStepCarriesAVerdict:
    def test_the_verdict_travels_with_the_pushed_step(self, world):
        world.use(READ, {"channel": "C123"})
        pushed = world.push.steps[-1]
        assert pushed["review"]["decision"] == "allowed"
        assert pushed["name"] == READ

    def test_a_sandbox_tool_is_labelled_honestly_as_observed_not_gated(self, world):
        world.resolution = router.resolve_tools(router.ResolutionInput(
            agent_allowed_tools=frozenset({"shell"}), grants=[]))
        world.handle("shell", {"command": "ls"})
        assert world.last["review"]["rule"] == "sandbox"
        assert "sandbox" in world.last["review"]["reason"]

    def test_a_tool_without_a_grant_is_denied_and_says_so(self, world):
        world.use("GMAIL_SEND_EMAIL", {"to": "x@example.com", "body": "hi"})
        assert world.last["review"]["decision"] == review.DENIED
        assert world.last["review"]["rule"] == "no_grant"

    def test_a_never_approvable_request_is_denied_with_its_rule(self, world):
        world.handle("request_approval", {"action": "aws.admin_credential",
                                          "arguments": {}, "why": "x"})
        assert world.last["review"]["decision"] == review.DENIED
        assert world.last["review"]["rule"] == "never"
        assert world.last["review"]["matched"] == "aws.admin_credential"

    def test_a_requested_approval_for_a_floor_tool_names_the_floor(self, world):
        approval = world.handle("request_approval", {
            "action": "email.send", "arguments": {"to": "x@example.com"}, "why": "reply"})["approval"]
        assert approval["policy"] == {
            "rule": "floor", "matched": "email.send",
            "reason": "on the always-approve floor"}
        assert world.last["review"]["decision"] == review.ASKED

    def test_a_handoff_says_no_access_travels_with_it(self, world):
        world.handle("handoff", {"to": "ops", "goal": "g", "state": "s", "requestedAction": "a"})
        assert "no access" in world.last["review"]["reason"]

    def test_the_verdict_is_stored_on_the_runs_own_event(self, world):
        world.use(READ, {"channel": "C123"})
        events = world.store.query(world.run["pk"], sk_prefix="EVT#")
        assert events[-1]["review"]["decision"] == "allowed"


class TestCardsComeFromToolCalls:
    def test_an_app_that_does_not_exist_becomes_no_card(self, world):
        world.handle("request_connector", {"connectorId": "nonexistentapp", "why": "x"})
        assert world.turn.cards == []
        assert world.last["review"]["decision"] == review.DENIED

    def test_an_already_connected_app_asks_for_nothing(self, world):
        world.handle("request_connector", {"connectorId": "slack", "why": "to post"})
        assert world.turn.cards == []
        assert "already connected" in world.last["summary"]

    def test_any_app_composio_offers_can_be_asked_for_not_just_a_short_list(self, world):
        world.handle("request_connector", {"connectorId": "github", "why": "To read the repo."})
        assert world.turn.cards == [{"type": "connect", "connectorId": "composio:github",
                                     "name": "GitHub", "why": "To read the repo."}]

    def test_an_app_the_bot_lacks_becomes_a_connect_card(self, world):
        world.handle("request_connector", {"connectorId": "gmail", "why": "To read the inbox."})
        assert world.turn.cards[0]["connectorId"] == "composio:gmail"
        assert world.last["review"]["decision"] == review.ALLOWED

    def test_a_routine_proposal_becomes_a_card_and_creates_nothing(self, world):
        world.handle("propose_routine", {"name": "Weekday planning", "schedule": "weekday-9",
                                         "prompt": "Draft the day."})
        assert world.turn.cards == [{"type": "routine", "name": "Weekday planning",
                                     "prompt": "Draft the day.", "preset": "weekday-9",
                                     "agentId": world.agent_id}]
        assert call("GET", "/routines")[1]["routines"] == []

    def test_a_routine_proposal_cannot_carry_an_expression(self, world):
        world.handle("propose_routine", {"name": "Too clever", "prompt": "x",
                                         "schedule": "cron(* * * * ? *)"})
        assert world.turn.cards == []
        assert world.last["review"]["decision"] == review.DENIED

    def test_a_bot_proposal_is_an_approval_not_a_card(self, world):
        # On a run the operator did not start. When their own message started it, the same
        # call creates the Bot at once (see test_bots_create_bots.py).
        world.run = {**world.run, "trigger": {"type": "routine"}}
        result = world.handle("propose_agent", {
            "name": "Calendar", "role": "Keeps the calendar.", "why": "A separate lane."})
        assert result["pause"] is True
        assert result["approval"]["action"] == "agent.create"
        assert result["approval"]["policy"]["rule"] == "floor"
        assert result["approval"]["policy"]["matched"] == "agent.create"

    def test_a_skill_proposal_is_an_approval_naming_its_rule(self, world):
        result = world.handle("propose_skill", {
            "name": "Weekly plan", "description": "Plans the week.", "body": "Steps...",
            "why": "Reusable."})
        assert result["approval"]["action"] == "skill.create"
        assert result["approval"]["policy"]["matched"] == "skill.create"


class TestWhatTheTurnLeavesBehind:
    def test_steps_and_cards_are_stored_on_the_message(self, world):
        world.use(READ, {"channel": "C123"})
        world.handle("propose_routine", {"name": "Digest", "schedule": "daily-730", "prompt": "Summarise."})
        orch._persist_message(world.store, world.run, world.agent, "Here you go.", world.cost,
                              steps=world.turn.steps, cards=world.turn.cards,
                              started_at="2026-09-20T09:00:00Z")
        rows = world.store.query(K.thread_pk(world.run["threadId"]), sk_prefix="MSG#")
        row = rows[-1]
        assert [s["name"] for s in row["steps"]] == [READ, "propose_routine"]
        assert row["steps"][0]["review"]["decision"] == "allowed"
        assert row["cards"][0]["type"] == "routine"
        assert row["startedAt"] == "2026-09-20T09:00:00Z" and row["endedAt"]

    def test_a_turn_with_no_words_still_keeps_its_trail_and_the_model_never_sees_it(self, world):
        world.use(WRITE, POST)   # pauses; the model said nothing
        orch._persist_message(world.store, world.run, world.agent, "", world.cost,
                              steps=world.turn.steps, cards=[])
        rows = world.store.query(K.thread_pk(world.run["threadId"]), sk_prefix="MSG#")
        assert rows[-1]["steps"][0]["review"]["decision"] == "asked"
        assert agentcore.build_messages(rows) == []

    def test_saving_to_memory_writes_the_fact_and_a_real_event(self, world):
        world.handle("remember", {"scope": "agent", "title": "Prefers bullets",
                                  "body": "Summaries as bullets, not prose."})
        memories = world.store.query(K.agent_pk(world.agent_id), sk_prefix="MEM#")
        assert [m["title"] for m in memories] == ["Prefers bullets"]
        rows = world.store.query(K.thread_pk(world.run["threadId"]), sk_prefix="MSG#")
        event = rows[-1]
        assert event["kind"] == "event" and event["role"] == "system"
        assert "Prefers bullets" in event["text"]

    def test_an_event_is_not_sent_to_the_model_and_does_not_make_a_thread_unread(self, world):
        thread = K.thread_pk(world.run["threadId"])
        world.store.put({"pk": thread, "sk": "META", "entity": "Thread",
                         "threadId": world.run["threadId"], "lastActivity": "2026-01-01T00:00:00Z"})
        threads.event(world.store, world.run["threadId"], "Routine created: X", icon="clock")
        assert world.store.get(thread, "META")["lastActivity"] == "2026-01-01T00:00:00Z"
        rows = world.store.query(thread, sk_prefix="MSG#")
        assert agentcore.build_messages(rows) == []


class TestAPausedOrRunningRunCanBeStopped:
    def _executing(self, world):
        run = runs.advance(world.store, world.run, RunState.PLANNING)
        return runs.advance(world.store, run, RunState.EXECUTING)

    def test_a_redirect_stops_the_run_and_waits_for_it_to_end(self, world):
        run = self._executing(world)
        stopped = api._stop_run(world.store, run, redirect_text="Do B instead")
        assert stopped["state"] == RunState.CANCELLING.value
        assert world.store.get(run["pk"], "META")["redirect"]["text"] == "Do B instead"
        # No second run beside the first: two on one session would interleave.
        assert len(world.store.query_index("gsi1", "gsi1pk", "RUNS")) == 1

    def test_when_the_old_run_has_ended_the_redirect_starts(self, world):
        run = self._executing(world)
        api._stop_run(world.store, run, redirect_text="Do B instead")
        new = orch._chain_redirect(world.store, run["pk"], world.agent)
        assert new["goal"] == "Do B instead"
        assert world.store.get(run["pk"], "META")["redirectedTo"] == new["runId"]
        assert new["trigger"]["redirectOf"] == run["runId"]

    def test_a_redirect_starts_only_one_run(self, world):
        run = self._executing(world)
        api._stop_run(world.store, run, redirect_text="Do B instead")
        first = orch._chain_redirect(world.store, run["pk"], world.agent)
        assert first is not None
        assert orch._chain_redirect(world.store, run["pk"], world.agent) is None

    def test_stopping_a_run_that_is_waiting_on_an_approval_actually_settles_it(self, world, monkeypatch):
        """Before: the stop flagged it CANCELLING and nothing was watching, so it
        stayed there for good -- the state a stop most needs to work in."""
        invoked = []
        monkeypatch.setattr(api, "_invoke_orchestrator",
                            lambda *a, **k: invoked.append(k))
        approval = world.use(WRITE, POST)["approval"]
        run = self._executing(world)
        run = runs.pause_for_approval(world.store, run, approval)

        api._stop_run(world.store, run)

        assert invoked == [{"cancel": True}], "nobody was told to settle the paused run"
        assert world.store.get(run["pk"], approvals_sk(approval))["status"] == approvals.DENIED
        out = orch._settle_paused_cancel(world.store, world.store.get(run["pk"], "META"))
        assert out["state"] == RunState.CANCELLED.value
        assert world.store.get(run["pk"], "META")["state"] == RunState.CANCELLED.value

    def test_stopping_twice_is_harmless(self, world):
        run = self._executing(world)
        first = api._stop_run(world.store, run)
        assert api._stop_run(world.store, first)["state"] == RunState.CANCELLING.value


def approvals_sk(approval):
    return K.approval_sk(approval["approvalId"])
