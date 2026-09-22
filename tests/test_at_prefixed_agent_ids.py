"""A Bot may name a teammate the way it was shown the name.

Every surface that introduces a teammate writes the id with an `@`. The team
directory emits `{"id": "@janai-williams", ...}`. A room roster reads
`Janai Williams (@janai-williams)`. An operator's mention is `@name`. So a Bot
asked to message Janai passes back `@janai-williams` -- exactly the string it was
given -- and the store has no `AGENT#@janai-williams`:

    Messaged a teammate — Denied
    blocked: no such active recipient '@janai-williams'

Five of those in one turn is what "the department heads cannot reach each other"
looked like from the outside. Nothing was wrong with the Bot's reasoning: a prompt
and a lookup disagreed about a format, and the prompt was the friendlier of the
two. So `@` is treated as how an id is *written*, not as part of one.

This does not loosen anything. A teammate that does not exist is still refused;
only the spelling is forgiven.
"""

import pytest

from amazai import agents as A, collab, keys as K

import handlers.orchestrator as orch

CHIEF = {"agentId": "chief", "name": "Chief", "status": "active",
         "budget": {"maxConcurrentRuns": 8}}
JANAI = {"agentId": "janai-williams", "name": "Janai Williams", "status": "active",
         "title": "Chief of Staff", "budget": {"maxConcurrentRuns": 8}}
TANIA = {"agentId": "tania-rodriguez", "name": "Tania Rodriguez", "status": "active",
         "title": "VP, Product & Engineering", "budget": {"maxConcurrentRuns": 8}}


@pytest.fixture
def agents(store):
    for row in (CHIEF, JANAI, TANIA):
        store.put({"pk": K.agent_pk(store.owner_id, row["agentId"]), "sk": "META",
                   "entity": "Agent", "gsi1pk": "AGENTS", "gsi1sk": row["name"], **row})
    return store


def run_of(agent_id="chief"):
    return {"runId": f"run_{agent_id}", "pk": K.run_pk(f"run_{agent_id}"),
            "agentId": agent_id, "trigger": {"type": "user"}}


class TestTheReferenceIsCleanedUpNotRejected:
    def test_an_at_prefix_is_not_part_of_an_id(self):
        assert A.agent_ref("@janai-williams") == "janai-williams"
        assert A.agent_ref("janai-williams") == "janai-williams"

    def test_whitespace_from_a_copied_directory_line_goes_too(self):
        assert A.agent_ref("  @janai-williams  ") == "janai-williams"
        assert A.agent_ref("@ janai-williams") == "janai-williams"

    def test_nothing_is_still_nothing(self):
        assert A.agent_ref(None) == "" and A.agent_ref("") == "" and A.agent_ref("@") == ""


class TestMessagingATeammateByTheNameItWasShown:
    def test_the_send_that_used_to_be_denied_now_reaches_them(self, agents):
        result = orch._message_agent(agents, run_of(), CHIEF,
                                     {"to": "@janai-williams", "text": "can you take this?"})
        assert result["message"]["recipientAgentId"] == "janai-williams"
        assert result["recipientName"] == "Janai Williams"

    def test_the_plain_id_still_works(self, agents):
        result = orch._message_agent(agents, run_of(), CHIEF,
                                     {"to": "janai-williams", "text": "hello"})
        assert result["message"]["recipientAgentId"] == "janai-williams"

    def test_a_teammate_who_does_not_exist_is_still_refused(self, agents):
        """The spelling is forgiven; the existence check is not."""
        with pytest.raises(collab.MessagingError, match="no such active recipient"):
            orch._message_agent(agents, run_of(), CHIEF,
                                {"to": "@nobody-at-all", "text": "hi"})

    def test_a_bot_still_cannot_message_itself_by_writing_its_own_handle(self, agents):
        with pytest.raises(collab.MessagingError, match="cannot message itself"):
            orch._message_agent(agents, run_of(), CHIEF, {"to": "@chief", "text": "hi"})

    def test_a_paused_teammate_is_still_refused(self, agents):
        agents.update(K.agent_pk(agents.owner_id, "janai-williams"), "META", {"status": "paused"})
        with pytest.raises(collab.MessagingError, match="no such active recipient"):
            orch._message_agent(agents, run_of(), CHIEF,
                                {"to": "@janai-williams", "text": "hi"})


class TestOpeningARoomWithTeammatesNamedThatWay:
    def test_members_given_with_an_at_are_found(self, agents, monkeypatch):
        monkeypatch.delenv("ORCHESTRATOR_FN_ARN", raising=False)
        monkeypatch.setattr(orch, "_invoke_orchestrator_async", lambda *a: None)
        result = orch._create_group_chat(agents, run_of(), CHIEF, {
            "title": "Business Operations", "goal": "set this quarter's priorities",
            "agentIds": ["@janai-williams", "@tania-rodriguez"]})
        assert set(result["agentIds"]) == {"chief", "janai-williams", "tania-rodriguez"}

    def test_a_member_who_does_not_exist_is_still_refused(self, agents, monkeypatch):
        monkeypatch.setattr(orch, "_invoke_orchestrator_async", lambda *a: None)
        with pytest.raises(ValueError, match="no such active Bot"):
            orch._create_group_chat(agents, run_of(), CHIEF, {
                "title": "Ghosts", "goal": "a goal",
                "agentIds": ["@nobody-at-all"]})


class TestLookingSomeoneUpThatWay:
    def test_the_roster_search_ignores_the_handle_marker(self, agents):
        found = [a["agentId"] for a in orch._find_agents(agents, "@janai-williams")]
        assert found == ["janai-williams"]

    def test_searching_by_plain_name_is_unchanged(self, agents):
        found = [a["agentId"] for a in orch._find_agents(agents, "tania")]
        assert found == ["tania-rodriguez"]


class TestHandingOffToThatName:
    def test_the_handoff_records_the_real_id(self, agents):
        handoff = orch._record_handoff(agents, run_of(), {"to": "@tania-rodriguez",
                                                          "goal": "own the roadmap"})
        assert handoff["toAgentId"] == "tania-rodriguez"
