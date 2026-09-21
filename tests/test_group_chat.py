"""How agents behave in a group chat.

Three things went wrong the first time a person put two agents in a room and said
hello, and each is pinned here:

* the first Bot ran its first-conversation menu at the whole room (its stored
  prompt says "your opening message asked..."; nothing said it was in a group);
* a question to both ("are you two able to work together?") woke only the lead,
  who then spoke for the other one;
* no agent knew who else was in the room, or the room's id, so none could pull a
  teammate in with `message_agent` even when that was the right thing to do.
"""

import pytest

import handlers.orchestrator as orch
from amazai import keys as K, onboarding, runs
from amazai.dispatch import addresses_everyone, targets_for

from tests.test_agents_api import api_table, call  # noqa: F401
from tests.test_drive_loop import text, world  # noqa: F401  (the fixture builds a drivable agent)


class TestWhoAMessageWakes:
    ROOM = {"kind": "room", "agentIds": ["eng", "chief"]}

    @pytest.mark.parametrize("message", [
        "Are you two able to work together on a few things?",
        "can you both look at this",
        "hey team, quick question",
        "Hi team",
        "thanks everyone",
        "@all status?",
        "@team status?",
        "you guys ready?",
    ])
    def test_a_message_to_the_whole_room_wakes_everyone(self, message):
        assert targets_for(self.ROOM, message) == ["eng", "chief"]

    @pytest.mark.parametrize("message", [
        "Fix the login bug",
        "our team roadmap is late",          # "team" in a sentence is not a call to the room
        "the all-hands is at three",
        "ask the team lead about it",
    ])
    def test_any_task_message_with_no_name_starts_the_whole_room(self, message):
        assert targets_for(self.ROOM, message) == ["eng", "chief"]

    def test_naming_someone_wakes_exactly_them_even_if_the_message_also_says_team(self):
        assert targets_for(self.ROOM, "hi team @chief plan my day") == ["chief"]

    def test_a_direct_thread_is_unchanged(self):
        assert targets_for({"kind": "dm", "agentIds": ["eng"]}, "hi team") == ["eng"]

    def test_the_check_itself(self):
        assert addresses_everyone("you two") and not addresses_everyone("")


class TestTheFirstBotsScript:
    def test_the_brief_is_recognised_and_a_rewrite_is_not(self):
        assert onboarding.is_brief(onboarding.brief("Chief"))
        assert onboarding.is_brief(onboarding.brief("Whoever"))   # a rename does not hide it
        assert not onboarding.is_brief("You run operations for the team.")
        assert not onboarding.is_brief(None)


def _bot(name, **extra):
    status, body = call("POST", "/agents", {
        "name": name, "role": extra.pop("role", f"{name} does its job."), "modelTier": "balanced",
        "avatar": {"shape": "cloud", "color": "#12a594"},
        "budget": {"perRunUsd": 1.0, "perMonthUsd": 10.0}, **extra})
    assert status == 201, body
    return body["agentId"]


class TestWhatAnAgentIsToldInARoom:
    @pytest.fixture
    def room(self, world):
        chief = _bot("Chief", entrypoint=True, title="Chief of staff", role="Runs the day.")
        eng = _bot("Engle", title="Sr Engineer", role="Repositories, tests and diagnostics.")
        for agent_id in (chief, eng):
            world.store.update(K.agent_pk(agent_id), "META", {
                "model": {"modelId": "test-model", "tier": "balanced"},
                "harnessArn": "arn:aws:bedrock-agentcore:us-west-2:1:harness/x"})
        status, thread = call("POST", "/threads", {"kind": "room", "title": "Eng Ops", "agentIds": [eng, chief]})
        assert status in (200, 201), thread
        world.chief, world.eng, world.room_id = chief, eng, thread["threadId"]
        return world

    def _prompt_for(self, w, agent_id, thread_id, goal="Hi team"):
        run = runs.create(w.store, agent_id=agent_id, thread_id=thread_id, goal=goal)
        fake = w.script([text("ok")])
        orch._drive(w.store, w.store.get(run["pk"], "META"), {"runId": run["runId"]})
        return fake.calls[0]["system_prompt"]

    def test_it_is_told_it_is_in_a_group_and_who_is_in_it(self, room):
        prompt = self._prompt_for(room, room.chief, room.room_id)
        assert '## This room' in prompt and '"Eng Ops"' in prompt
        assert "Engle (@" in prompt and "Sr Engineer" in prompt and "Repositories, tests" in prompt
        assert "Chief (@" not in prompt.split("## This room")[1], "it was listed among its own teammates"

    def test_it_is_given_the_room_id_so_it_can_actually_pull_a_teammate_in(self, room):
        prompt = self._prompt_for(room, room.eng, room.room_id)
        assert f'collaboration_context_id` "{room.room_id}"' in prompt
        assert "message_agent" in prompt and "priority` true" in prompt

    def test_it_is_told_not_to_speak_for_a_teammate(self, room):
        assert "Never answer on a teammate's behalf" in self._prompt_for(room, room.eng, room.room_id)

    def test_the_first_bot_does_not_run_its_first_conversation_menu_in_a_group(self, room):
        prompt = self._prompt_for(room, room.chief, room.room_id)
        assert "Your opening message asked" not in prompt
        assert "Managing my inbox" not in prompt
        assert "do not run a first-conversation menu" in prompt
        assert "You are Chief" in prompt, "its identity must survive"

    def test_but_it_keeps_the_script_in_a_private_chat(self, room):
        prompt = self._prompt_for(room, room.chief, f"dm-{room.chief}")
        assert "Your opening message asked" in prompt
        assert "## This room" not in prompt

    def test_a_prompt_the_owner_rewrote_is_kept_in_a_room(self, room):
        room.store.update(K.agent_pk(room.chief), "META", {"systemPrompt": "Always answer in haiku."})
        assert "Always answer in haiku." in self._prompt_for(room, room.chief, room.room_id)

    def test_a_direct_thread_gets_no_room_note(self, room):
        assert "## This room" not in self._prompt_for(room, room.eng, f"dm-{room.eng}")

    def test_it_is_told_to_take_a_lane_and_route_consequential_work_for_approval(self, room):
        prompt = self._prompt_for(room, room.eng, room.room_id)
        assert "begin work immediately" in prompt and "take one concrete low-risk lane" in prompt
        assert "routes it for operator approval" in prompt


class TestABotOpeningATaskRoom:
    def test_it_can_find_an_active_specialist_before_inviting_them(self, world):
        ops = _bot("Ops", title="Release", role="Owns deployment checks.")
        world.store.update(K.agent_pk(world.agent_id), "META", {"status": "active", "state": "active"})
        world.store.update(K.agent_pk(ops), "META", {"status": "active", "state": "active"})

        found = world.handle("find_agents", {"query": "deployment"})["toolResult"]["agents"]

        assert found == [{"agentId": ops, "name": "Ops", "title": "Release",
                          "role": "Owns deployment checks."}]

    def test_it_adds_itself_starts_every_member_and_keeps_approval_boundaries(self, world, monkeypatch):
        chief = world.agent_id
        ops = _bot("Ops", role="Owns deployment checks.")
        for agent_id in (chief, ops):
            world.store.update(K.agent_pk(agent_id), "META", {"status": "active", "state": "active"})

        woken = []
        monkeypatch.setattr(orch, "_invoke_orchestrator_async", lambda run_id, owner: woken.append(run_id))
        result = world.handle("create_group_chat", {
            "title": "Release readiness",
            "agentIds": [ops],
            "goal": "Assess the release, take separate lanes, and surface blockers.",
        })["toolResult"]

        assert result["created"] is True and result["agentIds"] == [chief, ops]
        room = world.store.get(K.thread_pk(result["threadId"]), "META")
        assert room["kind"] == "room" and room["createdBy"] == f"agent:{chief}"
        created_runs = [r for r in world.store.query_index("gsi1", "gsi1pk", "RUNS", limit=20)
                        if r["threadId"] == result["threadId"]]
        assert {r["agentId"] for r in created_runs} == {chief, ops}
        assert len(woken) == 2
        assert all(r["trigger"]["type"] == "group_chat" for r in created_runs)
        assert "own access and approvals" in world.last["review"]["reason"]

    def test_it_refuses_a_room_without_another_active_bot(self, world):
        world.store.update(K.agent_pk(world.agent_id), "META", {"status": "active", "state": "active"})
        result = world.handle("create_group_chat", {
            "title": "Solo room", "agentIds": [], "goal": "Do the work.",
        })["toolResult"]
        assert "at least one other active Bot" in result["error"]
