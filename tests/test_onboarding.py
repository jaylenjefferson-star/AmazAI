"""The first Bot and the greeting every new Bot gives.

Each test pins something that would fail quietly if it regressed: a greeting
that reaches the model as an assistant-first conversation, a second "first
Bot", a flag an agent could grant itself, an operator name that fails a create.
"""

import pytest

from amazai import agentcore, agents as A, keys as K, onboarding
from amazai.store import Conflict

import handlers.orchestrator as orchestrator

PERSON = A.Actor(user_id="user-1", org_id="org-1")
AN_AGENT = A.Actor(user_id="user-1", org_id="org-1", agent_id="eng")


def first_bot(**over):
    body = {"name": "Chief", "entrypoint": True,
            "avatar": {"shape": "pebble", "color": "#2f6fe4"}}
    body.update(over)
    return body


def a_bot(**over):
    body = {"name": "Cloud Operations", "role": "AWS investigations, logs, alarms.",
            "avatar": {"shape": "paper", "color": "#2f6fe4"}}
    body.update(over)
    return body


def greeting(plan):
    rows = [i for i in plan.items if i["entity"] == "Message"]
    assert len(rows) == 1, "a new Bot must have exactly one greeting"
    return rows[0]


class TestTheGreeting:
    def test_the_first_bot_greets_by_name_and_offers_the_closest_fits(self):
        plan = A.plan_create(first_bot(operatorName="Jaylen"), PERSON)
        row = greeting(plan)
        assert row["text"] == (
            "Hey Jaylen — good to meet you.\n\n"
            "What do you mainly want me for?\n\n"
            "Pick the closest fit, or type your own.")
        assert row["suggestions"] == list(onboarding.SUGGESTIONS)
        assert row["role"] == "assistant" and row["author"] == "Chief"

    def test_every_new_bot_greets_not_only_the_first(self):
        row = greeting(A.plan_create(a_bot(operatorName="Jaylen"), PERSON))
        assert row["text"].startswith("Hey Jaylen — good to meet you.")
        assert "What do you mainly want me for?" in row["text"]

    def test_a_bot_with_nothing_to_offer_does_not_say_pick_one(self):
        """"Pick the closest fit" over an empty row of options would be an
        instruction with nothing to pick."""
        row = greeting(A.plan_create(a_bot(), PERSON))
        assert "suggestions" not in row
        assert "closest fit" not in row["text"]

    def test_the_greeting_lives_on_the_bots_own_thread(self):
        plan = A.plan_create(a_bot(), PERSON)
        assert greeting(plan)["pk"] == K.thread_pk(PERSON.user_id, "dm-cloud-operations")

    def test_the_greeting_is_undone_with_the_agent(self):
        """A failed harness rolls back every row; a greeting left behind would
        be a conversation with an agent that does not exist."""
        plan = A.plan_create(a_bot(), PERSON)
        row = greeting(plan)
        assert (row["pk"], row["sk"]) in plan.rollback_keys

    @pytest.mark.parametrize("bad", [None, 7, "", "   ", "x" * 41, "Jay\nlen"])
    def test_an_unusable_operator_name_drops_the_name_rather_than_failing(self, bad):
        row = greeting(A.plan_create(a_bot(operatorName=bad), PERSON))
        assert row["text"].startswith("Hey — good to meet you.")

    def test_the_operator_name_is_not_stored_on_the_agent(self):
        plan = A.plan_create(a_bot(operatorName="Jaylen"), PERSON)
        assert "Jaylen" not in repr(plan.agent)


class TestTheModelNeverSeesTheGreeting:
    def test_a_greeting_is_left_out_of_the_conversation_sent_to_the_model(self):
        """Converse refuses a conversation that opens on an assistant turn."""
        plan = A.plan_create(first_bot(operatorName="Jaylen"), PERSON)
        history = [greeting(plan),
                   {"role": "user", "author": "you", "text": "Managing my inbox"}]
        messages = agentcore.build_messages(history)
        assert [m["role"] for m in messages] == ["user"]

    def test_ordinary_assistant_turns_are_untouched(self):
        history = [{"role": "user", "text": "hi"},
                   {"role": "assistant", "text": "hello"}]
        assert [m["role"] for m in agentcore.build_messages(history)] == ["user", "assistant"]


class TestTheFirstBot:
    def test_it_is_flagged_titled_and_given_the_server_side_brief(self):
        agent = A.plan_create(first_bot(), PERSON).agent
        assert agent["entrypoint"] is True
        assert agent["title"] == "Chief"
        assert agent["systemPrompt"] == onboarding.brief("Chief")

    def test_the_brief_names_every_option_the_greeting_offered(self):
        """The model never sees the greeting, so "the first one" has to mean
        something from the brief alone."""
        brief = onboarding.brief("Chief")
        assert all(option in brief for option in onboarding.SUGGESTIONS)

    def test_the_brief_asks_for_nothing_that_needs_a_computer(self):
        assert not any(word in " ".join(onboarding.SUGGESTIONS).lower()
                       for word in ("browse", "research", "shell", "terminal"))

    def test_a_prompt_the_caller_supplied_is_kept(self):
        agent = A.plan_create(first_bot(systemPrompt="Be terse."), PERSON).agent
        assert agent["systemPrompt"] == "Be terse."

    def test_an_ordinary_bot_is_not_an_entrypoint(self):
        agent = A.plan_create(a_bot(), PERSON).agent
        assert agent["entrypoint"] is False

    def test_a_second_first_bot_is_a_conflict(self):
        with pytest.raises(Conflict):
            A.plan_create(first_bot(name="Second"), PERSON, has_entrypoint=True)

    def test_an_ordinary_bot_is_fine_once_a_first_bot_exists(self):
        assert A.plan_create(a_bot(), PERSON, has_entrypoint=True).agent["entrypoint"] is False

    def test_an_agent_cannot_create_a_first_bot_either(self):
        with pytest.raises(A.Escalation):
            A.plan_create(first_bot(), AN_AGENT)

    def test_a_proposal_from_an_agent_cannot_carry_the_flag(self):
        """Agents propose children through an approval; the normalized proposal
        is an allowlist, and `entrypoint` must never be on it."""
        proposal = orchestrator._agent_creation_proposal(
            {"name": "Child", "role": "things", "entrypoint": True, "title": "Chief"},
            parent_agent_id="eng")
        assert "entrypoint" not in proposal

    @pytest.mark.parametrize("value", ["true", 1, "yes"])
    def test_the_flag_must_be_a_real_boolean(self, value):
        with pytest.raises(A.ValidationError):
            A.plan_create(first_bot(entrypoint=value), PERSON)


class TestTheFlagCannotBeMoved:
    EXISTING = {"agentId": "chief", "name": "Chief", "role": "things",
                "description": "", "systemPrompt": "things",
                "workingStyle": "collaborative", "title": "Chief",
                "avatar": {"shape": "pebble", "color": "#2f6fe4"},
                "entrypoint": True}

    def test_entrypoint_is_not_editable(self):
        with pytest.raises(A.ValidationError) as exc:
            A.plan_update(self.EXISTING, {"entrypoint": False}, PERSON)
        assert "not editable" in str(exc.value)

    def test_the_title_is_editable_and_audited_as_cosmetic(self):
        changes, events = A.plan_update(self.EXISTING, {"title": "Email"}, PERSON)
        assert changes["title"] == "Email"
        assert [e["action"] for e in events] == ["agent.updated"]

    def test_a_title_that_will_not_fit_a_chip_is_refused(self):
        with pytest.raises(A.ValidationError):
            A.plan_update(self.EXISTING, {"title": "x" * (A.TITLE_MAX + 1)}, PERSON)

    def test_a_bot_created_before_titles_existed_can_still_be_renamed(self):
        old = {k: v for k, v in self.EXISTING.items() if k not in {"title", "entrypoint"}}
        changes, _ = A.plan_update(old, {"name": "Ops"}, PERSON)
        assert changes["name"] == "Ops"
