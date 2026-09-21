"""What the composer sends, and what the API does with it -- through the real
handler. Routing, redirect, "Run now", members, memory correction, and the
history lines that real actions write."""

import re
from pathlib import Path

import pytest

import handlers.api as api
from amazai import approvals, dispatch, keys as K, routines, runs
from amazai.states import RunState
from amazai.store import Store

from tests.test_agents_api import api_table, call  # noqa: F401


def make_agent(name, **over):
    status, agent = call("POST", "/agents", {
        "name": name, "role": f"{name} does its job.", "modelTier": "balanced",
        "avatar": {"shape": "cloud", "color": "#12a594"},
        "budget": {"perRunUsd": 1.0, "perMonthUsd": 10.0}, **over})
    assert status == 201, agent
    return agent["agentId"]


def make_room(*agent_ids, title="Ship it"):
    status, room = call("POST", "/threads", {"kind": "room", "title": title,
                                             "agentIds": list(agent_ids)})
    assert status == 201, room
    return room["threadId"]


def messages(thread_id):
    return call("GET", f"/threads/{thread_id}")[1]["messages"]


def run_row(store, run_id):
    return store.get(K.run_pk(run_id), "META")


class TestMentions:
    def test_a_mention_is_a_whole_token_not_a_prefix(self):
        assert dispatch.mentioned(["eng", "engineering"], "@engineering please") == ["engineering"]
        assert dispatch.mentioned(["eng", "engineering"], "@eng please") == ["eng"]

    def test_a_mention_inside_a_word_or_email_does_not_count(self):
        assert dispatch.mentioned(["eng"], "mail me at x@eng.example.com") == []

    def test_each_agent_counts_once_in_membership_order(self):
        assert dispatch.mentioned(["a1", "b1"], "@b1 @a1 @b1") == ["a1", "b1"]

    def test_a_room_with_no_mention_starts_every_member(self):
        assert dispatch.targets_for({"kind": "room", "agentIds": ["a1", "b1"]}, "hello") == ["a1", "b1"]

    def test_a_direct_thread_always_wakes_its_one_bot(self):
        assert dispatch.targets_for({"kind": "dm", "agentIds": ["a1"]}, "@b1 hi") == ["a1"]


class TestParallelWakeInAChannel:
    def test_every_mentioned_bot_gets_its_own_run(self, api_table):
        eng, ops, res = make_agent("Eng"), make_agent("Ops"), make_agent("Res")
        room = make_room(eng, ops, res)
        status, body = call("POST", f"/threads/{room}/messages", {"text": "@eng @ops check the deploy"})
        assert status == 202
        assert {r["agentId"] for r in body["runs"]} == {eng, ops}
        assert len({r["runId"] for r in body["runs"]}) == 2

    def test_each_run_is_told_who_else_was_woken(self, api_table):
        store = Store("owner-a", table=api_table)
        eng, ops = make_agent("Eng"), make_agent("Ops")
        room = make_room(eng, ops)
        body = call("POST", f"/threads/{room}/messages", {"text": "@eng @ops go"})[1]
        trigger = run_row(store, body["runs"][0]["runId"])["trigger"]
        assert trigger["woke"] == ["Eng", "Ops"]

    def test_the_wake_is_a_real_history_line_in_the_room(self, api_table):
        eng, ops = make_agent("Eng"), make_agent("Ops")
        room = make_room(eng, ops)
        call("POST", f"/threads/{room}/messages", {"text": "@eng @ops go"})
        events = [m for m in messages(room) if m.get("kind") == "event"]
        assert [e["text"] for e in events] == ["Woke Eng and Ops"]

    def test_an_unmentioned_task_message_starts_the_whole_room(self, api_table):
        eng, ops = make_agent("Eng"), make_agent("Ops")
        room = make_room(eng, ops)
        body = call("POST", f"/threads/{room}/messages", {"text": "anyone?"})[1]
        assert [r["agentId"] for r in body["runs"]] == [eng, ops]

    def test_mentioning_one_of_two_prefix_named_bots_wakes_only_that_one(self, api_table):
        eng, engineering = make_agent("Eng"), make_agent("Engineering")
        room = make_room(eng, engineering)
        body = call("POST", f"/threads/{room}/messages", {"text": "@engineering hi"})[1]
        assert [r["agentId"] for r in body["runs"]] == [engineering]


class TestDirectMentionsAndSkills:
    def test_naming_another_bot_in_a_direct_thread_asks_for_a_handoff_not_a_wake(self, api_table):
        store = Store("owner-a", table=api_table)
        chief, cal = make_agent("Chief"), make_agent("Calendar")
        body = call("POST", f"/threads/dm-{chief}/messages", {"text": "@calendar book it"})[1]
        assert [r["agentId"] for r in body["runs"]] == [chief]
        assert run_row(store, body["runId"])["trigger"]["mentions"] == [cal]

    def _skill(self, agent_id, name="Deploy runbook"):
        status, skill = call("POST", "/skills", {"name": name, "description": "How we ship.",
                                                 "body": "1. Build. 2. Ship."})
        assert status == 201, skill
        assert call("POST", f"/skills/{skill['skillId']}/assignments",
                    {"agentId": agent_id})[0] == 201
        return skill["skillId"]

    def test_a_slash_that_names_an_assigned_skill_rides_on_the_run(self, api_table):
        store = Store("owner-a", table=api_table)
        bot = make_agent("Ship")
        self._skill(bot)
        body = call("POST", f"/threads/dm-{bot}/messages", {"text": "/deploy-runbook to staging"})[1]
        assert run_row(store, body["runId"])["trigger"]["skill"]["name"] == "Deploy runbook"

    def test_a_slash_that_names_nothing_is_just_text(self, api_table):
        store = Store("owner-a", table=api_table)
        bot = make_agent("Ship")
        status, body = call("POST", f"/threads/dm-{bot}/messages", {"text": "/etc/hosts is broken"})
        assert status == 202
        assert "skill" not in run_row(store, body["runId"])["trigger"]

    def test_a_skill_the_bot_was_not_assigned_is_not_invoked(self, api_table):
        store = Store("owner-a", table=api_table)
        assigned, other = make_agent("Ship"), make_agent("Other")
        self._skill(assigned)
        body = call("POST", f"/threads/dm-{other}/messages", {"text": "/deploy-runbook go"})[1]
        assert "skill" not in run_row(store, body["runId"])["trigger"]


class TestRedirectThroughTheApi:
    def _running(self, api_table):
        store = Store("owner-a", table=api_table)
        bot = make_agent("Chief")
        first = call("POST", f"/threads/dm-{bot}/messages", {"text": "Do A"})[1]["runId"]
        run = run_row(store, first)
        run = runs.advance(store, run, RunState.PLANNING)
        runs.advance(store, run, RunState.EXECUTING)
        return store, bot, first

    def test_a_message_sent_mid_run_redirects_it(self, api_table):
        store, bot, first = self._running(api_table)
        status, body = call("POST", f"/threads/dm-{bot}/messages",
                            {"text": "Actually do B", "redirectRunId": first})
        assert status == 202
        assert body["runs"][0]["redirected"] is True and body["runId"] == first
        row = run_row(store, first)
        assert row["state"] == RunState.CANCELLING.value
        assert row["redirect"]["text"] == "Actually do B"

    def test_the_redirected_message_is_in_the_thread_for_the_next_run(self, api_table):
        store, bot, first = self._running(api_table)
        call("POST", f"/threads/dm-{bot}/messages", {"text": "Actually do B", "redirectRunId": first})
        assert [m["text"] for m in messages(f"dm-{bot}") if m["role"] == "user"] == ["Do A", "Actually do B"]

    def test_a_redirect_of_a_finished_run_is_just_a_new_message(self, api_table):
        store, bot, first = self._running(api_table)
        store.update(K.run_pk(first), "META", {"state": RunState.COMPLETED.value})
        body = call("POST", f"/threads/dm-{bot}/messages",
                    {"text": "Do B", "redirectRunId": first})[1]
        assert body["runId"] != first and "redirected" not in body["runs"][0]

    def test_a_redirect_cannot_reach_a_run_in_another_thread(self, api_table):
        store, bot, first = self._running(api_table)
        other = make_agent("Other")
        body = call("POST", f"/threads/dm-{other}/messages",
                    {"text": "hi", "redirectRunId": first})[1]
        assert body["runId"] != first
        assert run_row(store, first)["state"] == RunState.EXECUTING.value

    def test_the_cancel_route_settles_a_run_waiting_on_an_approval(self, api_table, monkeypatch):
        invoked = []
        monkeypatch.setattr(api, "_invoke_orchestrator", lambda *a, **k: invoked.append(k))
        store, bot, first = self._running(api_table)
        run = run_row(store, first)
        approval = approvals.request(store, run, action="email.send", arguments={"to": "x"},
                                     why="reply", capability=__import__("amazai.policy", fromlist=["x"]).Capability.WRITE)
        runs.pause_for_approval(store, run_row(store, first), approval)
        status, _ = call("POST", f"/runs/{first}/cancel")
        assert status == 202
        assert {"cancel": True} in invoked
        assert store.get(run["pk"], K.approval_sk(approval["approvalId"]))["status"] == approvals.DENIED


class TestRunNow:
    def _routine(self, agent_id, **over):
        status, routine = call("POST", "/routines", {
            "name": "Morning brief", "agentId": agent_id, "prompt": "Draft the day.",
            "trigger": {"type": "schedule", "expression": "cron(0 9 ? * MON-FRI *)"}, **over})
        assert status == 201, routine
        return routine["routineId"]

    def test_run_now_starts_a_run_of_that_routine(self, api_table, monkeypatch):
        monkeypatch.setattr(api, "_schedule", lambda *a, **k: None)
        store = Store("owner-a", table=api_table)
        bot = make_agent("Chief")
        rid = self._routine(bot)
        status, body = call("POST", f"/routines/{rid}/run", headers={"idempotency-key": "k1"})
        assert status == 202 and body["ok"] is True
        run = run_row(store, body["runId"])
        assert run["agentId"] == bot and run["goal"] == "Draft the day."
        assert run["trigger"]["type"] == "manual" and run["trigger"]["routineId"] == rid

    def test_a_double_click_is_one_run(self, api_table, monkeypatch):
        monkeypatch.setattr(api, "_schedule", lambda *a, **k: None)
        bot = make_agent("Chief")
        rid = self._routine(bot)
        first = call("POST", f"/routines/{rid}/run", headers={"idempotency-key": "k1"})[1]
        again = call("POST", f"/routines/{rid}/run", headers={"idempotency-key": "k1"})[1]
        assert again["deduplicated"] is True and again["runId"] == first["runId"]

    def test_a_paused_routine_can_still_be_run_by_hand(self, api_table, monkeypatch):
        monkeypatch.setattr(api, "_schedule", lambda *a, **k: None)
        bot = make_agent("Chief")
        rid = self._routine(bot)
        call("PATCH", f"/routines/{rid}", {"enabled": False})
        status, body = call("POST", f"/routines/{rid}/run", headers={"idempotency-key": "k2"})
        assert status == 202 and body["ok"] is True

    def test_an_archived_routine_cannot_be_run(self, api_table, monkeypatch):
        monkeypatch.setattr(api, "_schedule", lambda *a, **k: None)
        bot = make_agent("Chief")
        rid = self._routine(bot)
        call("DELETE", f"/routines/{rid}")
        assert call("POST", f"/routines/{rid}/run")[0] == 409

    def test_creating_a_routine_writes_a_history_line_in_words(self, api_table, monkeypatch):
        monkeypatch.setattr(api, "_schedule", lambda *a, **k: None)
        bot = make_agent("Chief")
        self._routine(bot)
        events = [m for m in messages(f"dm-{bot}") if m.get("kind") == "event"]
        assert [e["text"] for e in events][-1] == "Routine created: Morning brief · weekdays at 9:00 AM"
        assert events[-1]["icon"] == "clock"

    def test_a_refused_schedule_leaves_no_history_line(self, api_table, monkeypatch):
        def refuse(*a, **k):
            raise RuntimeError("scheduler said no")
        monkeypatch.setattr(api, "_schedule", refuse)
        bot = make_agent("Chief")
        status, _ = call("POST", "/routines", {
            "name": "Nope", "agentId": bot, "prompt": "Draft the day.",
            "trigger": {"type": "schedule", "expression": "cron(0 9 ? * MON-FRI *)"}})
        assert status == 502
        assert [m for m in messages(f"dm-{bot}") if m.get("kind") == "event"] == []


class TestRoutinePresetsAndWording:
    def test_the_presets_a_bot_may_name_match_the_console_exactly(self):
        js = (Path(__file__).resolve().parents[1] / "web/src/schedules.js").read_text()
        console = dict(re.findall(r"key: '([a-z0-9-]+)', label: '[^']*', expression: '([^']+)'", js))
        console.pop("custom", None)
        assert console == routines.PRESETS, "web/src/schedules.js and routines.PRESETS drifted"

    @pytest.mark.parametrize("trigger,words", [
        ({"type": "schedule", "expression": "cron(0 9 ? * MON-FRI *)"}, "weekdays at 9:00 AM"),
        ({"type": "schedule", "expression": "cron(30 7 * * ? *)"}, "every day at 7:30 AM"),
        ({"type": "schedule", "expression": "cron(0 14 ? * MON-FRI *)"}, "weekdays at 2:00 PM"),
        ({"type": "schedule", "expression": "rate(1 hour)"}, "every hour"),
        ({"type": "schedule", "expression": "rate(30 minutes)"}, "every 30 minutes"),
        ({"type": "manual"}, "runs only when you start it"),
        ({"type": "schedule", "expression": "cron(0 9 1 * ? *)"}, "cron(0 9 1 * ? *)"),
    ])
    def test_a_schedule_is_said_in_words_or_left_as_written(self, trigger, words):
        assert routines.describe_schedule(trigger) == words


class TestRoomMembers:
    def test_members_can_be_added_and_removed_with_a_line_for_each(self, api_table):
        eng, ops, res = make_agent("Eng"), make_agent("Ops"), make_agent("Res")
        room = make_room(eng, ops)
        status, updated = call("PATCH", f"/threads/{room}", {"agentIds": [eng, res]})
        assert status == 200 and updated["agentIds"] == [eng, res]
        lines = [m["text"] for m in messages(room) if m.get("kind") == "event"]
        assert sorted(lines) == ["Ops left", "Res joined"]

    def test_the_cap_of_six_applies_to_adding_too(self, api_table):
        ids = [make_agent(f"Bot{c}") for c in "ABCDEFG"]
        room = make_room(*ids[:6])
        status, body = call("PATCH", f"/threads/{room}", {"agentIds": ids})
        assert status == 400 and "at most 6" in body["detail"]

    def test_a_room_cannot_be_emptied(self, api_table):
        eng = make_agent("Eng")
        room = make_room(eng)
        assert call("PATCH", f"/threads/{room}", {"agentIds": []})[0] == 400

    def test_only_real_agents_can_be_added(self, api_table):
        eng = make_agent("Eng")
        room = make_room(eng)
        assert call("PATCH", f"/threads/{room}", {"agentIds": [eng, "ghost"]})[0] == 400

    def test_a_direct_thread_has_no_members_to_change(self, api_table):
        eng = make_agent("Eng")
        assert call("PATCH", f"/threads/dm-{eng}", {"agentIds": [eng]})[0] == 400

    def test_a_finished_room_is_read_only(self, api_table):
        store = Store("owner-a", table=api_table)
        eng, ops = make_agent("Eng"), make_agent("Ops")
        room = make_room(eng)
        store.update(K.thread_pk(room), "META", {"status": "completed"})
        assert call("PATCH", f"/threads/{room}", {"agentIds": [eng, ops]})[0] == 409

    def test_a_room_can_be_renamed(self, api_table):
        eng = make_agent("Eng")
        room = make_room(eng)
        assert call("PATCH", f"/threads/{room}", {"title": "Launch week"})[1]["title"] == "Launch week"


class TestMemory:
    def test_saving_to_memory_writes_a_history_line(self, api_table):
        bot = make_agent("Chief")
        status, row = call("POST", f"/agents/{bot}/memory",
                           {"title": "Prefers bullets", "body": "Bullets, not prose."})
        assert status == 201
        events = [m for m in messages(f"dm-{bot}") if m.get("kind") == "event"]
        assert events[-1]["text"] == "Saved to memory: Prefers bullets"
        assert events[-1]["icon"] == "layers"

    def test_a_correction_edits_in_place_and_says_who(self, api_table):
        bot = make_agent("Chief")
        mem = call("POST", f"/agents/{bot}/memory", {"title": "Timezone", "body": "Pacific"})[1]
        status, fixed = call("PATCH", f"/agents/{bot}/memory/{mem['memId']}", {"body": "Eastern"})
        assert status == 200
        assert fixed["body"] == "Eastern" and fixed["correctedBy"] == "you" and fixed["correctedAt"]
        assert fixed["scope"] == "agent" and fixed["memId"] == mem["memId"]
        assert messages(f"dm-{bot}")[-1]["text"] == "Memory corrected: Timezone"

    def test_a_correction_cannot_move_a_fact_between_scopes(self, api_table):
        bot = make_agent("Chief")
        mem = call("POST", f"/agents/{bot}/memory", {"title": "T", "body": "x"})[1]
        call("PATCH", f"/agents/{bot}/memory/{mem['memId']}", {"scope": "shared_user", "body": "y"})
        row = call("GET", f"/agents/{bot}")[1]["memory"][0]
        assert row["scope"] == "agent"

    def test_promoting_to_foundational_pins_it(self, api_table):
        bot = make_agent("Chief")
        mem = call("POST", f"/agents/{bot}/memory", {"title": "T", "body": "x", "kind": "note",
                                                     "pinned": False})[1]
        fixed = call("PATCH", f"/agents/{bot}/memory/{mem['memId']}", {"kind": "foundational"})[1]
        assert fixed["pinned"] is True

    def test_an_empty_correction_is_refused(self, api_table):
        bot = make_agent("Chief")
        mem = call("POST", f"/agents/{bot}/memory", {"title": "T", "body": "x"})[1]
        assert call("PATCH", f"/agents/{bot}/memory/{mem['memId']}", {})[0] == 400

    def test_shared_memory_can_be_corrected_too(self, api_table):
        mem = call("POST", "/memory", {"title": "Name", "body": "Jay"})[1]
        assert call("PATCH", f"/memory/{mem['memId']}", {"body": "Jaylen"})[1]["body"] == "Jaylen"


class TestApprovedActionsWriteHistory:
    def _paused_with(self, api_table, action, arguments, capability):
        from amazai.policy import Capability
        store = Store("owner-a", table=api_table)
        bot = make_agent("Chief")
        rid = call("POST", f"/threads/dm-{bot}/messages", {"text": "go"})[1]["runId"]
        run = run_row(store, rid)
        run = runs.advance(store, runs.advance(store, run, RunState.PLANNING), RunState.EXECUTING)
        approval = approvals.request(store, run, action=action, arguments=arguments,
                                     why="because", capability=Capability(capability))
        runs.pause_for_approval(store, run, approval)
        return bot, rid, approval

    def test_approving_a_skill_saves_it_and_says_so(self, api_table):
        bot, rid, approval = self._paused_with(
            api_table, "skill.create",
            {"name": "Weekly plan", "description": "Plans the week.", "body": "Steps",
             "proposedBy": "chief"}, "admin")
        status, body = call("POST", f"/approvals/{rid}/{approval['approvalId']}", {"approve": True})
        assert status == 200 and body["createdSkill"]["name"] == "Weekly plan"
        assert messages(f"dm-{bot}")[-1]["text"] == "Saved as a skill: Weekly plan"

    def test_approving_shared_memory_publishes_it_and_says_so(self, api_table):
        bot, rid, approval = self._paused_with(
            api_table, "memory.publish",
            {"title": "Timezone", "body": "Pacific", "kind": "foundational", "proposedBy": "chief"},
            "write")
        call("POST", f"/approvals/{rid}/{approval['approvalId']}", {"approve": True})
        assert messages(f"dm-{bot}")[-1]["text"] == "Shared with every Bot: Timezone"
        assert [m["title"] for m in call("GET", "/memory")[1]["memory"]] == ["Timezone"]

    def test_denying_writes_no_history_line_because_nothing_happened(self, api_table):
        bot, rid, approval = self._paused_with(
            api_table, "skill.create",
            {"name": "Weekly plan", "description": "d", "body": "b", "proposedBy": "chief"}, "admin")
        call("POST", f"/approvals/{rid}/{approval['approvalId']}", {"approve": False})
        assert [m for m in messages(f"dm-{bot}") if m.get("kind") == "event"] == []


class TestARoutineFireClaimsTheRealRun:
    def test_a_duplicate_delivery_gets_a_run_that_exists(self, api_table, monkeypatch):
        """The claimed id used to be a throwaway; the duplicate was handed the id of
        a run that was never created."""
        monkeypatch.setattr(api, "_schedule", lambda *a, **k: None)
        store = Store("owner-a", table=api_table)
        bot = make_agent("Chief")
        rid = call("POST", "/routines", {"name": "Brief", "agentId": bot, "prompt": "Draft the day.",
                                         "trigger": {"type": "schedule",
                                                     "expression": "cron(0 9 ? * MON-FRI *)"}})[1]["routineId"]
        routine = store.get(K.routine_pk(rid), "META")
        fired = routines.fire(store, routine, invoke=lambda r: None, idempotency_key="dup")
        dup = routines.fire(store, routine, invoke=lambda r: None, idempotency_key="dup")
        assert dup["deduplicated"] is True and dup["runId"] == fired["runId"]
        assert store.get(K.run_pk(dup["runId"]), "META")["runId"] == dup["runId"]

    def test_a_failed_fire_releases_its_claim_so_a_retry_can_run(self, api_table, monkeypatch):
        monkeypatch.setattr(api, "_schedule", lambda *a, **k: None)
        store = Store("owner-a", table=api_table)
        bot = make_agent("Chief")
        rid = call("POST", "/routines", {"name": "Brief", "agentId": bot, "prompt": "Draft the day.",
                                         "trigger": {"type": "schedule",
                                                     "expression": "cron(0 9 ? * MON-FRI *)"}})[1]["routineId"]
        routine = store.get(K.routine_pk(rid), "META")
        real = runs.create
        monkeypatch.setattr(runs, "create", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(RuntimeError):
            routines.fire(store, routine, invoke=lambda r: None, idempotency_key="retry")
        monkeypatch.setattr(runs, "create", real)
        retried = routines.fire(store, routine, invoke=lambda r: None, idempotency_key="retry")
        assert retried.get("deduplicated") is not True and retried["ok"] is True


class TestSavingASkillFromAConversation:
    def test_it_says_so_in_that_conversation(self, api_table):
        bot = make_agent("Chief")
        status, skill = call("POST", "/skills", {
            "name": "Weekly plan", "description": "Plans the week.", "body": "Steps",
            "sourceThreadId": f"dm-{bot}"})
        assert status == 201 and "sourceThreadId" not in skill
        assert messages(f"dm-{bot}")[-1]["text"] == "Saved as a skill: Weekly plan"

    def test_a_thread_that_does_not_exist_is_ignored_not_an_error(self, api_table):
        status, _ = call("POST", "/skills", {"name": "Ghost skill", "description": "d",
                                              "body": "b", "sourceThreadId": "dm-nobody"})
        assert status == 201

    def test_without_a_source_nothing_is_written_anywhere(self, api_table):
        bot = make_agent("Chief")
        call("POST", "/skills", {"name": "Plain skill", "description": "d", "body": "b"})
        assert [m for m in messages(f"dm-{bot}") if m.get("kind") == "event"] == []
