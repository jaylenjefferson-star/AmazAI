"""A Bot makes a Bot when the operator asks, and the loop lets it finish the job.

Two things had to be true for "ask Chief to make three Bots" to work at all, and
neither was:

* **A tool's answer has to reach the model.** An inline tool ends the model's stream
  at the call. The loop ran the tool, dropped the result, and finished the turn, so a
  Bot could not act on what a connector returned or on the id of a Bot it had just made.
* **The operator's own request has to be enough.** A Bot could only *propose* a Bot,
  which stopped at a card and reached provisioning without a model.

What is enforced here, in code, is what makes it safe to drop the card for a request the
operator made: only a run their own message started may create; the child holds no more
than its creator; there is no cap on how many but the organisation's own ceiling; and
anything the operator did not start still ends in a card.
"""

import json

import pytest

import handlers.orchestrator as orch
from amazai import agents as A, agentcore, collab, keys as K, provisioning, runs
from amazai.states import RunState

from tests.loop_world import SLACK, World
from tests.test_agents_api import api_table, call  # noqa: F401
from tests.test_drive_loop import FakeCore, text, tool_use, world  # noqa: F401

SCOUT = {"name": "Scout", "title": "Research", "role": "Finds and summarises sources.",
         "description": "Owns market research. Produces a one-page brief. Never emails anyone without approval."}


@pytest.fixture(autouse=True)
def no_aws(monkeypatch):
    """Creating a Bot's harness is the one thing that needs an account."""
    def provision(store, agent):
        return store.update(K.agent_pk(agent["agentId"]), "META", {
            "harnessArn": f"arn:aws:bedrock-agentcore:us-west-2:1:harness/amazai_{agent['agentId']}",
            "executionRoleArn": "arn:aws:iam::1:role/dynamic", "status": "active", "state": "active"})
    monkeypatch.setattr(provisioning, "provision_harness", provision)
    monkeypatch.delenv("MAX_AGENTS", raising=False)


@pytest.fixture
def woken(monkeypatch):
    """Every run the orchestrator was asked to start, in order."""
    started = []
    monkeypatch.setattr(orch, "_invoke_orchestrator_async", lambda run_id, owner: started.append(run_id))
    return started


def agent_row(world, agent_id):  # noqa: F811
    return world.store.get(K.agent_pk(agent_id), "META")


def create(world, **over):  # noqa: F811
    return world.handle("create_agent", {**SCOUT, **over})


def all_agents(world):  # noqa: F811
    return {a["agentId"] for a in world.store.query_index("gsi1", "gsi1pk", "AGENTS", limit=200)}


def started_by(world, trigger):  # noqa: F811
    """Make this run one the operator's own message did not start."""
    world.store.update(world.run["pk"], "META", {"trigger": trigger})
    world.run = world.store.get(world.run["pk"], "META")


class TestCreatingABotWhenTheOperatorAsked:
    def test_the_bot_exists_at_once_and_the_model_is_told_its_id(self, world, woken):  # noqa: F811
        result = create(world)

        assert result["pause"] is False
        out = result["toolResult"]
        assert out["created"] is True and out["agentId"] == "scout" and out["reportsTo"] == world.agent_id
        assert agent_row(world, "scout")["status"] == "active"
        assert agent_row(world, "scout")["harnessArn"].endswith("amazai_scout")

    def test_it_reports_to_its_creator_and_is_recorded_as_its_creators(self, world, woken):  # noqa: F811
        create(world)
        row = agent_row(world, "scout")
        assert row["parentAgentId"] == world.agent_id
        assert row["reportsTo"] == world.agent_id
        _, listed = call("GET", "/agents")
        assert {a["agentId"]: a["managerId"] for a in listed["agents"]}["scout"] == world.agent_id

    def test_it_uses_the_title_and_standing_orders_it_was_given(self, world, woken):  # noqa: F811
        create(world)
        row = agent_row(world, "scout")
        assert (row["title"], row["role"]) == ("Research", "Finds and summarises sources.")
        assert "Never emails anyone without approval" in row["description"]

    def test_it_holds_the_apps_its_creator_holds(self, world, woken):  # noqa: F811
        create(world)
        grants = world.store.query(K.agent_pk("scout"), sk_prefix="GRANT#")
        assert [(g["connectorId"], g["capability"], g["allowedTools"]) for g in grants] == [
            (SLACK, "admin", ["*"])]

    def test_it_starts_with_the_standard_budget_not_a_reduced_one(self, world, woken):  # noqa: F811
        create(world)
        budget = agent_row(world, "scout")["budget"]
        assert (budget["perRunUsd"], budget["perMonthUsd"]) == (1.0, 20.0)     # what the operator's own Bots get
        assert budget["onCeiling"] == "hard_stop"                               # the stop is unchanged

    def test_it_is_audited_with_the_creating_bot_named(self, world, woken):  # noqa: F811
        create(world)
        audit = [a for a in world.store.query(K.agent_pk("scout"), sk_prefix="AUDIT#")
                 if a["action"] == "agent.created"]
        assert len(audit) == 1
        assert world.agent_id in json.dumps(audit[0])
        assert "at the operator's request" in audit[0]["detail"]

    def test_the_transcript_says_who_made_what(self, world, woken):  # noqa: F811
        create(world)
        events = [m["text"] for m in world.messages() if m.get("kind") == "event"]
        assert any(t == f"{world.agent['name']} created Scout" for t in events)
        assert world.push.steps and world.push.steps[-1]["name"] == "agent.created"

    def test_the_old_tool_name_a_harness_may_still_carry_does_the_same(self, world, woken):  # noqa: F811
        assert world.handle("propose_agent", SCOUT)["toolResult"]["created"] is True


class TestNothingItCreatesCanHoldMoreThanItsCreator:
    def test_a_read_only_bot_makes_read_only_bots(self, api_table, monkeypatch, woken):  # noqa: F811
        w = World(api_table, monkeypatch, grants=[{"connectorId": SLACK, "capability": "read",
                                                   "allowedTools": ["*"]}])
        w.store.update(K.agent_pk(w.agent_id), "META", {"model": {"modelId": "m", "tier": "balanced"}})

        assert w.handle("create_agent", SCOUT)["toolResult"]["created"] is True

        grants = w.store.query(K.agent_pk("scout"), sk_prefix="GRANT#")
        assert [(g["connectorId"], g["capability"]) for g in grants] == [(SLACK, "read")]

    def test_a_bot_with_no_apps_makes_bots_with_none(self, api_table, monkeypatch, woken):  # noqa: F811
        w = World(api_table, monkeypatch, grants=[])
        w.store.update(K.agent_pk(w.agent_id), "META", {"model": {"modelId": "m", "tier": "balanced"}})

        w.handle("create_agent", SCOUT)

        assert w.store.query(K.agent_pk("scout"), sk_prefix="GRANT#") == []

    def test_the_model_cannot_ask_for_more_by_saying_so(self, world, woken):  # noqa: F811
        create(world, grants=[{"connectorId": "composio:github", "capability": "admin", "allowedTools": ["*"]}],
               budget={"perMonthUsd": 500, "perRunUsd": 100}, allowedTools=["everything"])

        row = agent_row(world, "scout")
        assert world.store.query(K.agent_pk("scout"), sk_prefix="GRANT#")[0]["connectorId"] == SLACK
        assert row["budget"]["perMonthUsd"] == 20.0
        assert "everything" not in row["allowedTools"]

    def test_optional_tools_are_only_ones_the_creator_has(self, world, woken):  # noqa: F811
        # The creator holds the terminal and files, not a browser: asking for one changes nothing.
        create(world, tools=["browser", "code_interpreter"])
        assert agent_row(world, "scout")["allowedTools"] == ["shell", "file_operations"]

    def test_a_tool_the_creator_has_can_be_passed_on(self, world, woken):  # noqa: F811
        world.store.update(K.agent_pk(world.agent_id), "META",
                           {"allowedTools": ["shell", "file_operations", "browser"]})
        world.agent = agent_row(world, world.agent_id)
        create(world, tools=["browser", "code_interpreter"])
        assert agent_row(world, "scout")["allowedTools"] == ["shell", "file_operations", "browser"]


class TestThereIsNoCapOnHowManyButTheOrganisations:
    def test_a_bot_can_make_as_many_as_it_is_asked_for(self, world, woken):  # noqa: F811
        for i in range(30):
            out = create(world, name=f"Helper {i:02d}")["toolResult"]
            assert out["created"] is True, out
        assert len(all_agents(world)) == 31                      # the creator and thirty

    def test_the_organisations_own_ceiling_is_the_only_limit_and_it_says_so(self, world, woken, monkeypatch):  # noqa: F811
        monkeypatch.setenv("MAX_AGENTS", "3")
        assert create(world, name="One")["toolResult"]["created"] is True
        assert create(world, name="Two")["toolResult"]["created"] is True
        refused = create(world, name="Three")["toolResult"]
        assert "error" in refused and "3 agents" in refused["error"]
        assert "three" not in all_agents(world)

    def test_the_default_ceiling_is_what_the_console_can_list(self):
        # Every roster read stops at 200 rows; past that a Bot would exist and not appear.
        assert A.DEFAULT_MAX_AGENTS == 200


class TestWhenSomethingGoesWrong:
    def test_a_bad_request_says_why_and_leaves_nothing_behind(self, world, woken):  # noqa: F811
        before = all_agents(world)
        result = create(world, name="x", modelTier="enormous")
        assert "error" in result["toolResult"] and result["pause"] is False
        assert all_agents(world) == before

    def test_a_name_already_taken_is_refused_with_the_reason(self, world, woken):  # noqa: F811
        assert create(world)["toolResult"]["created"] is True
        again = create(world)["toolResult"]
        assert "already exists" in again["error"] and "different name" in again["error"]

    def test_a_harness_that_cannot_be_made_is_rolled_back_and_audited(self, world, woken, monkeypatch):  # noqa: F811
        def boom(store, agent):
            raise RuntimeError("AccessDeniedException: not allowed")
        monkeypatch.setattr(provisioning, "provision_harness", boom)

        result = create(world)["toolResult"]

        assert "nothing was left behind" in result["error"]
        assert "scout" not in all_agents(world)                          # the rows were removed
        failed = [a for a in world.store.query(K.agent_pk("scout"), sk_prefix="AUDIT#")
                  if a["action"] == "agent.provision_failed"]
        assert len(failed) == 1                                           # the attempt is still on record


class TestWhenTheOperatorDidNotAsk:
    @pytest.mark.parametrize("trigger", [
        {"type": "routine"},
        {"type": "agent", "fromAgentId": "somebody"},
        {"type": "schedule"},
    ])
    def test_a_bot_acting_on_its_own_still_ends_in_a_card_and_makes_nothing(self, world, woken, trigger):  # noqa: F811
        started_by(world, trigger)
        before = all_agents(world)

        result = create(world)

        assert result["pause"] is True and result["approval"]["action"] == "agent.create"
        assert result["approval"]["policy"]["rule"] == "floor"
        assert all_agents(world) == before

    def test_the_card_carries_the_title_the_model_chose(self, world, woken):  # noqa: F811
        started_by(world, {"type": "routine"})
        assert create(world)["approval"]["arguments"]["title"] == "Research"

    def test_an_agent_actor_is_still_refused_by_the_plan_itself(self):
        actor = A.Actor(user_id="u", org_id="o", agent_id="chief")
        with pytest.raises(A.Escalation):
            A.plan_create({"name": "Scout", "role": "x"}, actor)

    def test_the_exception_needs_an_agent_and_the_operators_request_together(self):
        person = A.Actor(user_id="u", org_id="o")
        plan = A.plan_create({"name": "Scout", "role": "Researches."}, person, on_owners_request=True)
        assert plan.agent["parentAgentId"] is None and "reportsTo" not in plan.agent   # nothing invented for a person


class TestBriefingTheNewBot:
    def test_a_first_task_starts_it_on_that_job(self, world, woken):  # noqa: F811
        out = create(world, firstTask="Summarise the three biggest rivals. Done = one page.")["toolResult"]

        assert out["briefed"] is True and woken == [out["runId"]]
        run = world.store.get(K.run_pk(out["runId"]), "META")
        assert run["agentId"] == "scout" and run["threadId"] == "dm-scout"
        assert run["goal"] == "Summarise the three biggest rivals. Done = one page."
        assert run["trigger"]["fromAgentId"] == world.agent_id

    def test_both_transcripts_say_who_briefed_whom(self, world, woken):  # noqa: F811
        create(world, firstTask="Summarise the rivals.")
        lines = [m["text"] for m in world.store.query(K.thread_pk("dm-scout"), sk_prefix="MSG#")
                 if m.get("kind") == "event"]
        assert any("briefed Scout: Summarise the rivals." in t for t in lines)

    def test_without_a_first_task_it_waits_and_the_model_is_told(self, world, woken):  # noqa: F811
        out = create(world)["toolResult"]
        assert out["briefed"] is False and "waiting" in out["briefing"] and woken == []

    def test_a_bot_that_cannot_start_yet_is_told_why_instead_of_being_started(self, world, woken, monkeypatch):  # noqa: F811
        monkeypatch.setattr(collab, "may_wake_now", lambda store, agent, limits: (False, "over budget"))
        out = create(world, firstTask="Go.")["toolResult"]
        assert out["created"] is True and out["briefed"] is False and "over budget" in out["briefing"]
        assert woken == []

    def test_the_new_bot_is_asked_its_job_as_its_whole_conversation(self, world, woken, monkeypatch):  # noqa: F811
        # A Bot never spoken to has only a greeting, which is not sent to the model.
        out = create(world, firstTask="Summarise the rivals.")["toolResult"]
        child_run = world.store.get(K.run_pk(out["runId"]), "META")
        fake = FakeCore([text("On it.")])
        monkeypatch.setattr(orch.agentcore, "AgentCore", fake)

        orch._drive(world.store, child_run, {"runId": child_run["runId"]})

        assert fake.calls[0]["messages"] == [{"role": "user", "content": [{"text": "Summarise the rivals."}]}]


class TestRefiningABotYouMade:
    def refine(self, world, **args):  # noqa: F811
        return world.handle("update_agent", {"agentId": "scout", **args})["toolResult"]

    def test_the_creator_can_sharpen_the_standing_orders(self, world, woken):  # noqa: F811
        create(world)
        out = self.refine(world, description="Owns market research. Cite every claim.", title="Insights")
        assert out == {"updated": ["description", "title"], "agentId": "scout"}
        row = agent_row(world, "scout")
        assert (row["title"], row["description"]) == ("Insights", "Owns market research. Cite every claim.")

    def test_the_change_is_audited_like_a_persons(self, world, woken):  # noqa: F811
        create(world)
        self.refine(world, role="Finds sources and cites them.")
        assert "agent.updated" in [a["action"] for a in world.store.query(K.agent_pk("scout"), sk_prefix="AUDIT#")]

    def test_only_a_bot_it_created_can_be_refined(self, world, woken):  # noqa: F811
        status, other = call("POST", "/agents", {"name": "Ledger", "role": "Keeps the books."})
        assert status == 201
        out = world.handle("update_agent", {"agentId": "ledger", "description": "Now works for me."})["toolResult"]
        assert "only refine a Bot you created" in out["error"]
        assert agent_row(world, "ledger")["description"] != "Now works for me."

    def test_it_only_works_when_the_operators_own_message_started_the_turn(self, world, woken):  # noqa: F811
        create(world)
        started_by(world, {"type": "routine"})
        out = self.refine(world, description="Quietly changed.")
        assert "operator's own message" in out["error"]
        assert "Quietly changed." not in agent_row(world, "scout")["description"]

    def test_access_budget_and_status_are_not_something_it_can_touch(self, world, woken):  # noqa: F811
        create(world)
        out = self.refine(world, budget={"perMonthUsd": 500}, allowedTools=["browser"], status="paused",
                          grants=[{"connectorId": "composio:github"}])
        assert "nothing to change" in out["error"]                       # none of those is a field it accepts
        row = agent_row(world, "scout")
        assert (row["budget"]["perMonthUsd"], row["status"]) == (20.0, "active")

    def test_a_refusal_says_why(self, world, woken):  # noqa: F811
        create(world)
        assert "nothing to change" in self.refine(world)["error"]
        assert "only refine a Bot you created" in world.handle("update_agent", {"agentId": "ghost", "title": "x"})["toolResult"]["error"]


class TestAToolsAnswerGoesBackToTheModel:
    """The loop hands an inline tool's result back and lets the model go on."""

    @pytest.fixture(autouse=True)
    def native(self, monkeypatch):
        monkeypatch.setenv("AMAZAI_CONTINUATION", "tool_result")

    def said(self, world):  # noqa: F811
        return [m for m in world.messages() if m["role"] == "assistant"][-1]

    def test_the_model_is_asked_again_with_what_the_tool_returned(self, world):  # noqa: F811
        remember = {"title": "Pacific time", "body": "Schedule in America/Los_Angeles."}
        fake = world.script([text("Noting that."), *tool_use("remember", remember)], [text("Saved it.")])

        result = world.drive()

        assert result["ok"] is True and world.state() == "COMPLETED"
        assert len(fake.calls) == 2
        asked, answered = fake.calls[1]["messages"][-2:]
        assert asked["role"] == "assistant"
        assert asked["content"][0] == {"text": "Noting that."}                       # what it said is kept
        assert asked["content"][1]["toolUse"]["name"] == "remember"
        assert answered["role"] == "user"
        tool_result = answered["content"][0]["toolResult"]
        assert tool_result["toolUseId"] == "tu-1" and tool_result["status"] == "success"
        assert json.loads(tool_result["content"][0]["text"])["saved"] is True

    def test_what_it_said_before_and_after_reads_as_one_reply_in_two_paragraphs(self, world):  # noqa: F811
        world.script([text("Noting that."), *tool_use("remember", {"title": "t", "body": "b"})],
                     [text("Saved it.")])
        world.drive()
        assert self.said(world)["text"] == "Noting that.\n\nSaved it."

    def test_the_whole_turn_is_one_message_with_every_step_in_it(self, world):  # noqa: F811
        world.script([*tool_use("remember", {"title": "t", "body": "b"})],
                     [*tool_use("remember", {"title": "u", "body": "c"}, "tu-2")], [text("Both saved.")])
        world.drive()
        assistants = [m for m in world.messages() if m["role"] == "assistant" and not m.get("starter")]
        assert len(assistants) == 1
        assert [s["name"] for s in assistants[0]["steps"]] == ["remember", "remember"]

    def test_calls_made_together_are_answered_together(self, world):  # noqa: F811
        fake = world.script([*tool_use("remember", {"title": "a", "body": "1"}, "tu-1", 1),
                             *tool_use("remember", {"title": "b", "body": "2"}, "tu-2", 2)],
                            [text("Both.")])
        world.drive()
        asked, answered = fake.calls[1]["messages"][-2:]
        assert [b["toolUse"]["toolUseId"] for b in asked["content"]] == ["tu-1", "tu-2"]
        assert [b["toolResult"]["toolUseId"] for b in answered["content"]] == ["tu-1", "tu-2"]

    def test_an_error_is_handed_back_as_an_error_so_the_model_can_react(self, world):  # noqa: F811
        fake = world.script([*tool_use("remember", {"title": "t", "body": "b", "scope": "everyone"})],
                            [text("That scope is not allowed; saving to my own memory instead.")])
        world.drive()
        tool_result = fake.calls[1]["messages"][-1]["content"][0]["toolResult"]
        assert tool_result["status"] == "error"
        assert "scope" in tool_result["content"][0]["text"]

    def test_a_connector_read_returns_its_data_to_the_model(self, world):  # noqa: F811
        from tests.fake_composio import READ
        fake = world.script([*tool_use("connector_call", {"tool": READ, "arguments": {"channel": "C123"}})],
                            [text("Here is what I found.")])
        world.drive()
        tool_result = fake.calls[1]["messages"][-1]["content"][0]["toolResult"]
        assert tool_result["status"] == "success"
        assert "C123" in tool_result["content"][0]["text"]                            # the read's own data

    def test_a_bot_that_creates_a_bot_is_told_its_id_and_can_say_so(self, world, woken):  # noqa: F811
        fake = world.script([text("Making it."), *tool_use("create_agent", SCOUT)],
                            [text("Done: Scout is on your roster.")])

        world.drive()

        assert "scout" in all_agents(world)
        told = json.loads(fake.calls[1]["messages"][-1]["content"][0]["toolResult"]["content"][0]["text"])
        assert told["created"] is True and told["agentId"] == "scout"
        assert self.said(world)["text"].endswith("Done: Scout is on your roster.")

    def test_a_tool_that_runs_inside_the_harness_is_not_answered_by_the_code(self, world):  # noqa: F811
        fake = world.script([text("Running it."), *tool_use("shell", {"command": "ls"})])
        world.drive()
        assert len(fake.calls) == 1                                                  # the harness ran it itself

    def test_an_approval_pauses_the_run_and_is_not_answered_here(self, world):  # noqa: F811
        from tests.fake_composio import WRITE
        from tests.loop_world import POST
        fake = world.script([*tool_use("connector_call", {"tool": WRITE, "arguments": POST})])
        assert world.drive()["state"] == RunState.AWAITING_APPROVAL.value
        assert len(fake.calls) == 1

    def test_a_stream_that_fails_after_a_tool_is_a_failure_not_a_second_round(self, world):  # noqa: F811
        def dies(_kw):
            yield from tool_use("remember", {"title": "t", "body": "b"})
            raise RuntimeError("Task timed out after 30s")
        fake = world.script(dies, [text("never reached")])
        world.drive()
        assert len(fake.calls) == 1 and world.state() == "RETRYING"

    def test_a_loop_of_tool_calls_is_stopped_and_says_so(self, world, monkeypatch):  # noqa: F811
        monkeypatch.setattr(orch, "MAX_TOOL_ROUNDS", 3)
        forever = [lambda kw, i=i: tool_use("remember", {"title": f"t{i}", "body": "b"}, f"tu-{i}")
                   for i in range(10)]
        fake = world.script(*forever)

        world.drive()

        assert len(fake.calls) == 4                                                  # the first ask and three rounds
        assert world.state() == "COMPLETED"
        assert "more tool calls than one turn allows" in self.said(world)["text"]

    def test_a_turn_that_has_run_as_long_as_one_may_stops_cleanly_before_the_worker_is_killed(self, world, monkeypatch):  # noqa: F811
        monkeypatch.setattr(orch, "ROUND_BUDGET_SECONDS", -1)
        fake = world.script([*tool_use("remember", {"title": "t", "body": "b"})], [text("never asked for")])

        world.drive()

        assert len(fake.calls) == 1 and world.state() == "COMPLETED"
        assert "ran as long as one turn may" in self.said(world)["text"]

    def test_without_the_native_shape_the_result_goes_as_a_plain_turn(self, world, monkeypatch):  # noqa: F811
        monkeypatch.setenv("AMAZAI_CONTINUATION", "resume_note")
        fake = world.script([text("Noting."), *tool_use("remember", {"title": "t", "body": "b"})],
                            [text("Saved.")])
        world.drive()
        asked, answered = fake.calls[1]["messages"][-2:]
        assert "toolUse" not in json.dumps(asked) and "toolResult" not in json.dumps(answered)
        assert "remember ->" in answered["content"][0]["text"]

    def test_every_inline_tool_the_code_answers_hands_back_something_readable(self, world):  # noqa: F811
        for name, args in [
            ("remember", {"title": "t", "body": "b"}),
            ("handoff", {"to": "someone", "goal": "g"}),
            ("propose_routine", {"name": "Digest", "schedule": "daily-730", "prompt": "Summarise."}),
            ("request_connector", {"connectorId": "slack", "why": "to post"}),
        ]:
            out = world.handle(name, args)
            assert out["pause"] is False and isinstance(out.get("toolResult", {"ok": True}), dict), name


class TestTheApprovalPathNowReachesProvisioning:
    def test_a_proposal_approved_from_a_card_is_given_the_accounts_model(self, api_table, monkeypatch):  # noqa: F811
        # A person's create reuses the model an existing Bot resolved; this path did not, so an
        # approved proposal reached provisioning with no model and was refused.
        from amazai.store import Store
        import handlers.api as api
        status, first = call("POST", "/agents", {"name": "Ledger", "role": "Keeps the books."})
        store = Store("owner-a", table=api_table)
        store.update(K.agent_pk(first["agentId"]), "META",
                     {"model": {"modelId": "resolved-model", "tier": "balanced"}})
        proposal = orch._agent_creation_proposal(
            {"name": "Scout", "role": "Researches.", "description": "x"}, parent_agent_id="ledger")

        created = api._create_approved_agent(store, proposal, A.Actor(user_id="owner-a", org_id="org-1"))

        assert created["model"]["modelId"] == "resolved-model"

    def test_the_rule_itself(self):
        agent = {"model": {"modelId": None, "tier": "balanced"}}
        provisioning.resolve_model_id(agent, [{"model": {"modelId": None}}, {"model": {"modelId": "m-1"}}])
        assert agent["model"]["modelId"] == "m-1"
        agent = {"model": {"modelId": "own", "tier": "deep"}}
        provisioning.resolve_model_id(agent, [{"model": {"modelId": "m-1"}}])
        assert agent["model"]["modelId"] == "own"                                     # never overwritten


class TestTheToolDescriptionsTeachTheLogic:
    """The selection heuristics are the model's to follow, so they are what its tool says."""

    desc = agentcore.INLINE_TOOLS["create_agent"]["description"]

    @pytest.mark.parametrize("phrase", [
        "Do not create one for a one-off task",                # one-off vs recurring
        "standing orders",                                     # durable rules in the description
        "Never put a secret in it",
        "This week's list",                                    # this week's task goes in a message
        "reads on a roster",                                   # name for scanability
        "reports to you",                                      # who it answers to
        "never more",                                          # authority inheritance, said out loud
        "firstTask",                                           # and the brief
    ])
    def test_it_says(self, phrase):
        assert phrase in self.desc

    def test_it_no_longer_says_a_new_bot_starts_with_no_apps(self):
        assert "no connector grants" not in self.desc

    def test_the_old_name_is_not_declared_on_new_harnesses(self):
        assert "propose_agent" not in agentcore.INLINE_TOOLS and "propose_agent" in orch.ROUND_TRIP_TOOLS



class TestABriefingRelayedAsOneLine:
    """The live failure: nine denials in one turn, all the same message.

    Chief was relaying a briefing written the way people write them --
    "Janai Williams — Chief of Staff, Operations" -- and the em dash was not in
    the name pattern. The message it got back restated the rule without naming
    the offending character or echoing what had been sent, so the only move
    left was to send the same string again.
    """

    BRIEFED = [
        ("Janeisha Carter \u2014 President, Chief of AI Strategy", "Janeisha Carter"),
        ("Janai Williams \u2014 Chief of Staff, Operations", "Janai Williams"),
        ("Tania Rodriguez \u2014 VP, Product & Engineering", "Tania Rodriguez"),
        ("Kiana Mitchell \u2014 VP, Growth & Customer Experience", "Kiana Mitchell"),
        ("Imani Brooks \u2014 VP, People & Agent Performance", "Imani Brooks"),
    ]

    @pytest.mark.parametrize("briefed,expected", BRIEFED)
    def test_each_one_is_created_rather_than_denied(self, world, woken, briefed, expected):  # noqa: F811
        result = create(world, name=briefed)
        assert result["toolResult"].get("created") is True, result["toolResult"]
        assert result["toolResult"]["name"] == expected

    def test_the_title_lands_in_the_title_not_the_name(self, world, woken):  # noqa: F811
        create(world, name="Janai Williams \u2014 Chief of Staff, Operations", title="")
        row = agent_row(world, "janai-williams")
        assert row["name"] == "Janai Williams"
        assert row["title"] == "Chief of Staff"

    def test_the_agent_id_comes_from_the_name_alone(self, world, woken):  # noqa: F811
        # The id is slugged from the name, so a title left in it produced
        # `janai-williams-chief-of-staff-operations` as the Bot's identity.
        create(world, name="Tania Rodriguez \u2014 VP, Product & Engineering", title="")
        assert "tania-rodriguez" in all_agents(world)

    def test_all_five_can_be_created_in_sequence(self, world, woken):  # noqa: F811
        for briefed, _ in self.BRIEFED:
            assert create(world, name=briefed, title="")["toolResult"].get("created") is True
        made = all_agents(world) - {world.agent_id}
        assert len(made) == 5, f"expected five Bots, got {sorted(made)}"

    def test_a_curly_apostrophe_is_not_a_refusal(self, world, woken):  # noqa: F811
        result = create(world, name="Sha\u2019Ron O\u2019Brien")
        assert result["toolResult"].get("created") is True
        assert result["toolResult"]["name"] == "Sha'Ron O'Brien"


class TestARefusalCanBeActedOn:
    def test_the_offending_character_is_named(self, world, woken):  # noqa: F811
        out = create(world, name="Ops [EU]")["toolResult"]
        assert "error" in out
        assert "'['" in out["error"], f"still not actionable: {out['error']}"

    def test_the_rejected_value_is_echoed_back(self, world, woken):  # noqa: F811
        out = create(world, name="Talent Scout!")["toolResult"]
        assert "Talent Scout!" in out["error"]

    def test_an_over_long_name_says_which_field_to_use_instead(self, world, woken):  # noqa: F811
        out = create(world, name="Janai Williams, who owns internal operations, project "
                                 "execution, process quality and company follow-through")["toolResult"]
        assert "`title`" in out["error"] and "`role`" in out["error"]

    def test_nothing_is_created_when_the_name_is_refused(self, world, woken):  # noqa: F811
        create(world, name="Ops [EU]")
        assert all_agents(world) == {world.agent_id}


class TestAProposalCarriesTheTidiedName:
    def test_the_card_shows_the_normalized_name(self, world, woken):  # noqa: F811
        # `_propose_agent` validated the profile and then stored the untouched
        # arguments, so the tidying was undone on the way to the operator.
        started_by(world, {"type": "agent"})
        result = create(world, name="Imani Brooks \u2014 VP, People & Agent Performance", title="")
        assert result["pause"] is True
        assert result["approval"]["arguments"]["name"] == "Imani Brooks"

    def test_the_card_shows_a_title_lifted_out_of_the_name(self, world, woken):  # noqa: F811
        started_by(world, {"type": "agent"})
        result = create(world, name="Janai Williams \u2014 Chief of Staff, Operations", title="")
        assert result["approval"]["arguments"]["title"] == "Chief of Staff"
