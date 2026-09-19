"""API-level coverage for docs/architecture/16-grokbot-ux-alignment.md:
memory scope/kind, the shared skills library, and the skill-proposal
approval gate.
"""

import pytest

from amazai import approvals, keys as K, runs
from amazai.policy import Capability
from amazai.states import RunState

from tests.test_agents_api import api_table, call  # noqa: F401


class TestAgentMemoryKind:
    def test_default_kind_matches_legacy_pinned_behaviour(self, api_table):
        call("POST", "/agents", {
            "name": "Engineering", "role": "Repos and PRs.",
            "modelTier": "balanced",
        })
        status, mem = call("POST", "/agents/engineering/memory",
                           {"title": "Deploy process", "body": "Prod needs a tag."})
        assert status == 201
        assert mem["scope"] == "agent"
        assert mem["kind"] == "foundational"
        assert mem["pinned"] is True

    def test_note_kind_is_not_pinned(self, api_table):
        call("POST", "/agents", {"name": "Engineering", "role": "Repos.",
                                 "modelTier": "balanced"})
        status, mem = call("POST", "/agents/engineering/memory",
                           {"title": "Scratch", "body": "temp", "kind": "note",
                            "pinned": False})
        assert status == 201
        assert mem["kind"] == "note"
        assert mem["pinned"] is False

    def test_unknown_kind_is_rejected(self, api_table):
        call("POST", "/agents", {"name": "Engineering", "role": "Repos.",
                                 "modelTier": "balanced"})
        status, err = call("POST", "/agents/engineering/memory",
                           {"title": "x", "body": "y", "kind": "bogus"})
        assert status == 400
        assert err["error"] == "invalid_request"


class TestSharedUserMemory:
    def test_shared_memory_is_written_under_the_user_not_an_agent(self, api_table):
        status, mem = call("POST", "/memory", {"title": "Timezone", "body": "America/Chicago"})
        assert status == 201
        assert mem["scope"] == "shared_user"
        assert mem["pk"] == K.user_pk("owner-a")

    def test_shared_memory_is_listed_and_deletable(self, api_table):
        call("POST", "/memory", {"title": "Timezone", "body": "America/Chicago"})
        status, listing = call("GET", "/memory")
        assert status == 200
        assert len(listing["memory"]) == 1
        mem_id = listing["memory"][0]["memId"]

        status, _ = call("DELETE", f"/memory/{mem_id}")
        assert status == 204
        assert call("GET", "/memory")[1]["memory"] == []


class TestSkillsApi:
    def test_a_person_authored_skill_is_active_immediately(self, api_table):
        status, skill = call("POST", "/skills", {
            "name": "Deploy runbook",
            "description": "Roll back a bad prod deploy.",
            "body": "1. Find the bad commit. 2. Revert. 3. Redeploy.",
        })
        assert status == 201
        assert skill["status"] == "active"
        assert skill["scope"] == "org"

        status, listing = call("GET", "/skills")
        assert status == 200
        assert [s["skillId"] for s in listing["skills"]] == ["deploy-runbook"]

    def test_invalid_skill_is_refused(self, api_table):
        status, err = call("POST", "/skills", {"name": "", "description": "x", "body": ""})
        assert status == 400
        assert err["error"] == "invalid_request"

    def test_editing_a_skill(self, api_table):
        call("POST", "/skills", {"name": "Deploy runbook", "description": "v1", "body": "b"})
        status, updated = call("PATCH", "/skills/deploy-runbook", {"description": "v2"})
        assert status == 200
        assert updated["description"] == "v2"

    def test_deleting_a_skill(self, api_table):
        call("POST", "/skills", {"name": "Deploy runbook", "description": "v1", "body": "b"})
        status, _ = call("DELETE", "/skills/deploy-runbook")
        assert status == 204
        assert call("GET", "/skills")[1]["skills"] == []


class TestSkillProposalApprovalGate:
    """An agent's `propose_skill` call reaches this exact path: a pending
    `skill.create` approval, decided by a person through `_decide`."""

    def test_approving_creates_the_skill_active(self, api_table, store):
        call("POST", "/agents", {"name": "Engineering", "role": "Repos.",
                                 "modelTier": "balanced"})
        run = runs.create(store, agent_id="engineering", thread_id="dm-engineering",
                          goal="propose a skill")
        run = runs.advance(store, run, RunState.PLANNING)
        run = runs.advance(store, run, RunState.EXECUTING)
        proposal = {
            "name": "Deploy runbook", "description": "Roll back safely.",
            "body": "revert then redeploy", "proposedBy": "engineering",
        }
        apv = approvals.request(
            store, run, action="skill.create", arguments=proposal,
            why="Future runs need this", capability=Capability.ADMIN)
        runs.pause_for_approval(store, run, apv)

        import handlers.api as api
        status, result = call(
            "POST", f"/approvals/{run['runId']}/{apv['approvalId']}", {"approve": True})
        assert status == 200
        assert result["createdSkill"]["status"] == "active"
        assert result["createdSkill"]["proposedBy"] == "engineering"

        listed = call("GET", "/skills")[1]["skills"]
        assert [s["status"] for s in listed] == ["active"]

    def test_denying_creates_nothing(self, api_table, store):
        call("POST", "/agents", {"name": "Engineering", "role": "Repos.",
                                 "modelTier": "balanced"})
        run = runs.create(store, agent_id="engineering", thread_id="dm-engineering",
                          goal="propose a skill")
        run = runs.advance(store, run, RunState.PLANNING)
        run = runs.advance(store, run, RunState.EXECUTING)
        proposal = {"name": "Deploy runbook", "description": "x", "body": "y",
                    "proposedBy": "engineering"}
        apv = approvals.request(
            store, run, action="skill.create", arguments=proposal,
            why="x", capability=Capability.ADMIN)
        runs.pause_for_approval(store, run, apv)

        status, result = call(
            "POST", f"/approvals/{run['runId']}/{apv['approvalId']}", {"approve": False})
        assert status == 200
        assert "createdSkill" not in result
        assert call("GET", "/skills")[1]["skills"] == []
