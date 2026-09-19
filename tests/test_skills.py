"""Skills: a shared, versioned playbook library -- assignment, not blanket
injection. See docs/architecture/17-message-and-memory-authorization.md §3.
"""

import pytest

from amazai import keys as K, skills as S


def a_skill(**over):
    body = {
        "name": "Deploy runbook",
        "description": "Steps to roll back a bad prod deploy safely.",
        "body": "1. Identify the bad commit. 2. git revert. 3. redeploy.",
        "owner": "user-1",
        "allowedTools": ["shell"],
        "allowedCapabilities": ["exec"],
    }
    body.update(over)
    return body


class TestValidation:
    def test_a_well_formed_skill_normalizes(self):
        fields = S.validate_skill(a_skill())
        assert fields["name"] == "Deploy runbook"
        assert fields["allowedTools"] == ["shell"]

    def test_empty_name_is_refused(self):
        with pytest.raises(S.ValidationError):
            S.validate_skill(a_skill(name=""))

    def test_empty_description_is_refused(self):
        with pytest.raises(S.ValidationError):
            S.validate_skill(a_skill(description=""))

    def test_oversized_body_is_refused(self):
        with pytest.raises(S.ValidationError):
            S.validate_skill(a_skill(body="x" * (S.MAX_BODY + 1)))

    def test_bad_test_status_is_refused(self):
        with pytest.raises(S.ValidationError):
            S.validate_skill(a_skill(testStatus="vibes"))


class TestCreate:
    def test_person_authored_skill_is_active_immediately_at_v1(self, store):
        meta = S.create(store, a_skill(), created_by="user-1")
        assert meta["status"] == "active"
        assert meta["proposedBy"] is None
        assert meta["currentVersion"] == 1
        assert meta["owner"] == "user-1"
        v1 = store.get(K.skill_pk(meta["skillId"]), K.skill_version_sk(1))
        assert v1["entity"] == "SkillVersion"
        assert v1["allowedTools"] == ["shell"]

    def test_agent_proposed_skill_is_not_active(self, store):
        """The whole point: an agent's proposal cannot enter any agent's
        context until a person has read it and approved it."""
        meta = S.create(store, a_skill(), created_by="user-1", proposed_by="eng")
        assert meta["status"] == "proposed"
        assert meta["proposedBy"] == "eng"

    def test_skill_id_is_derived_from_the_name(self, store):
        meta = S.create(store, a_skill(), created_by="user-1")
        assert meta["skillId"] == "deploy-runbook"

    def test_meta_and_v1_are_written_atomically(self, store):
        """A crash between the two writes should never be possible --
        assert both exist by construction, not just that create() returns."""
        meta = S.create(store, a_skill(), created_by="user-1")
        assert store.get(K.skill_pk(meta["skillId"]), "META")
        assert store.get(K.skill_pk(meta["skillId"]), K.skill_version_sk(1))


class TestActiveSkills:
    def test_only_active_rows_are_selected(self):
        rows = [
            {"status": "active", "name": "a"},
            {"status": "proposed", "name": "b"},
            {"status": "disabled", "name": "c"},
        ]
        assert [r["name"] for r in S.active_skills(rows)] == ["a"]

    def test_activate_flips_status_and_keeps_provenance(self, store):
        meta = S.create(store, a_skill(), created_by="user-1", proposed_by="eng")
        changes = S.activate(meta)
        assert changes["status"] == "active"
        # proposedBy is untouched by the returned patch -- provenance never
        # changes, only who may use it.
        assert "proposedBy" not in changes


class TestVersioning:
    def test_a_wording_only_change_does_not_need_approval(self, store):
        meta = S.create(store, a_skill(), created_by="user-1")
        v1 = S.latest_version(store, meta["skillId"])
        new_fields = S.validate_skill(a_skill(description="Updated wording only."))
        assert S.version_needs_approval(v1, new_fields) is False

    def test_a_tool_grant_change_needs_approval(self, store):
        meta = S.create(store, a_skill(), created_by="user-1")
        v1 = S.latest_version(store, meta["skillId"])
        new_fields = S.validate_skill(a_skill(allowedTools=["shell", "browser"]))
        assert S.version_needs_approval(v1, new_fields) is True

    def test_an_approval_requirement_change_needs_approval(self, store):
        meta = S.create(store, a_skill(), created_by="user-1")
        v1 = S.latest_version(store, meta["skillId"])
        new_fields = S.validate_skill(a_skill(approvalRequired=True))
        assert S.version_needs_approval(v1, new_fields) is True

    def test_versions_are_immutable_once_applied(self, store):
        meta = S.create(store, a_skill(), created_by="user-1")
        v1 = S.latest_version(store, meta["skillId"])
        fields = S.validate_skill(a_skill(allowedTools=["shell", "browser"]))
        pending = S.propose_version(fields, skill_id=meta["skillId"], next_version=2,
                                    created_by="user-1")
        S.apply_version(store, meta["skillId"], pending, approved_by="user-1")

        # v1 itself never changed; it is a separate, still-readable row.
        v1_again = store.get(K.skill_pk(meta["skillId"]), K.skill_version_sk(1))
        assert v1_again["allowedTools"] == v1["allowedTools"]
        v2 = store.get(K.skill_pk(meta["skillId"]), K.skill_version_sk(2))
        assert v2["allowedTools"] == ["shell", "browser"]
        assert store.get(K.skill_pk(meta["skillId"]), "META")["currentVersion"] == 2


class TestAssignment:
    def test_an_unassigned_active_skill_is_not_injected(self, store):
        S.create(store, a_skill(), created_by="user-1")
        assert S.assigned_active_skills(store, "eng") == []

    def test_an_assigned_active_skill_is_returned_at_its_assigned_version(self, store):
        meta = S.create(store, a_skill(), created_by="user-1")
        S.assign(store, skill_id=meta["skillId"], agent_id="eng", version=1,
                assigned_by="user-1")
        result = S.assigned_active_skills(store, "eng")
        assert len(result) == 1
        assert result[0]["skillId"] == meta["skillId"]
        assert result[0]["assignedVersion"] == 1

    def test_a_proposed_skill_is_not_returned_even_if_assigned(self, store):
        """Assignment cannot itself make an unapproved skill usable."""
        meta = S.create(store, a_skill(), created_by="user-1", proposed_by="eng")
        S.assign(store, skill_id=meta["skillId"], agent_id="eng", version=1,
                assigned_by="user-1")
        assert S.assigned_active_skills(store, "eng") == []

    def test_assignment_pins_the_version_a_later_bump_does_not_move(self, store):
        meta = S.create(store, a_skill(), created_by="user-1")
        S.assign(store, skill_id=meta["skillId"], agent_id="eng", version=1,
                assigned_by="user-1")
        fields = S.validate_skill(a_skill(allowedTools=["shell", "browser"]))
        pending = S.propose_version(fields, skill_id=meta["skillId"], next_version=2,
                                    created_by="user-1")
        S.apply_version(store, meta["skillId"], pending, approved_by="user-1")

        result = S.assigned_active_skills(store, "eng")
        assert result[0]["assignedVersion"] == 1
        assert result[0]["allowedTools"] == ["shell"]

    def test_unassign_removes_it_from_context(self, store):
        meta = S.create(store, a_skill(), created_by="user-1")
        S.assign(store, skill_id=meta["skillId"], agent_id="eng", version=1,
                assigned_by="user-1")
        S.unassign(store, skill_id=meta["skillId"], agent_id="eng")
        assert S.assigned_active_skills(store, "eng") == []


class TestBoundedTools:
    def test_a_skill_cannot_grant_a_tool_the_agent_was_never_given(self):
        version = {"allowedTools": ["shell", "browser", "aws_cli"]}
        resolved = frozenset({"shell"})
        assert S.bounded_tools(version, resolved) == frozenset({"shell"})

    def test_declaring_no_tools_leaves_resolution_untouched(self):
        version = {"allowedTools": []}
        resolved = frozenset({"shell", "browser"})
        assert S.bounded_tools(version, resolved) == frozenset({"shell", "browser"})

    def test_a_narrower_declaration_actually_narrows(self):
        version = {"allowedTools": ["shell"]}
        resolved = frozenset({"shell", "browser"})
        assert S.bounded_tools(version, resolved) == frozenset({"shell"})
