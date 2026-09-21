"""Who reports to whom.

The rule the owner gave: every Bot reports into Chief unless it was started, or
later moved, under someone else. These tests pin that, and the two things around
it that must not move: a reporting line is *organization, not authority*, and
only a person can draw one.
"""

import pytest

from amazai import agents as A, keys as K, org
from amazai.agents import ValidationError

import handlers.orchestrator as orch

from tests.test_agents_api import api_table, call  # noqa: F401


def row(agent_id, *, reports_to=None, entrypoint=False, status="active", name=None):
    r = {"agentId": agent_id, "name": name or agent_id.title(), "status": status,
         "entrypoint": entrypoint}
    if reports_to is not None:
        r["reportsTo"] = reports_to
    return r


CHIEF = row("chief", entrypoint=True)


class TestTheDefault:
    def test_a_bot_with_nothing_chosen_reports_to_chief(self):
        assert org.resolve([CHIEF, row("eng"), row("ops")]) == {
            "chief": None, "eng": "chief", "ops": "chief"}

    def test_chief_reports_to_the_owner(self):
        assert org.resolve([CHIEF])["chief"] is None

    def test_with_no_chief_yet_everyone_reports_to_the_owner(self):
        # The deployed account before Chief exists: Engineering alone.
        assert org.resolve([row("eng")]) == {"eng": None}

    def test_an_older_bot_needs_no_migration(self):
        # No `reportsTo` on the row at all, which is every Bot made before the field.
        old = {"agentId": "eng", "name": "Engineering", "state": "active"}
        assert org.resolve([CHIEF, old])["eng"] == "chief"


class TestStartingOtherwise:
    def test_a_bot_can_report_to_another_bot(self):
        rows = [CHIEF, row("eng"), row("qa", reports_to="eng")]
        assert org.resolve(rows)["qa"] == "eng"

    def test_a_bot_can_report_straight_to_the_owner(self):
        rows = [CHIEF, row("eng", reports_to="owner")]
        assert org.resolve(rows)["eng"] is None

    def test_a_manager_who_leaves_moves_their_team_up_to_chief(self):
        rows = [CHIEF, row("eng", status="archived"), row("qa", reports_to="eng")]
        assert org.resolve(rows)["qa"] == "chief"       # not into a hole, not off the chart

    def test_a_team_whose_chief_leaves_reports_to_the_owner(self):
        rows = [row("chief", entrypoint=True, status="archived"), row("eng")]
        assert org.resolve(rows) == {"eng": None}

    def test_a_stored_self_reference_is_ignored_not_believed(self):
        rows = [CHIEF, row("eng", reports_to="eng")]
        assert org.resolve(rows)["eng"] == "chief"

    def test_a_loop_that_got_stored_anyway_is_cut_so_the_chart_still_has_a_top(self):
        # Two edits racing can close one; `validate` cannot see the other in flight.
        rows = [CHIEF, row("a", reports_to="b"), row("b", reports_to="a")]
        manager = org.resolve(rows)
        assert set(manager) == {"chief", "a", "b"}
        for start in manager:                      # every Bot ends at the owner
            seen, cursor = set(), start
            while cursor is not None:
                assert cursor not in seen
                seen.add(cursor)
                cursor = manager[cursor]


class TestReads:
    def test_annotating_adds_the_manager_and_leaves_what_was_stored_alone(self):
        rows = [CHIEF, row("eng")]
        out = org.annotate(rows)
        assert [r["managerId"] for r in out] == [None, "chief"]
        assert "reportsTo" not in out[1]           # default stays "not chosen"
        assert "managerId" not in rows[1]          # the input was not mutated

    def test_who_reports_to_a_bot(self):
        manager = org.resolve([CHIEF, row("ops"), row("eng")])
        assert org.reports_of("chief", manager) == ["eng", "ops"]
        assert org.reports_of("eng", manager) == []


class TestValidate:
    ROWS = [CHIEF, row("eng"), row("qa", reports_to="eng"), row("old", status="archived")]

    def test_a_live_bot_or_the_owner_is_fine(self):
        assert org.validate("qa", "chief", self.ROWS) == "chief"
        assert org.validate("qa", "owner", self.ROWS) == "owner"

    @pytest.mark.parametrize("target, why", [
        ("qa", "itself"),
        ("nobody", "not an active Bot"),
        ("old", "not an active Bot"),          # archived
        ("", "Bot id"),
        (None, "Bot id"),
        (7, "Bot id"),
    ])
    def test_it_refuses_what_is_not_a_place_to_report(self, target, why):
        with pytest.raises(ValidationError, match=why):
            org.validate("qa", target, self.ROWS)

    def test_it_will_not_put_a_bot_under_its_own_team(self):
        with pytest.raises(ValidationError, match="loop"):
            org.validate("eng", "qa", self.ROWS)         # qa already reports to eng
        with pytest.raises(ValidationError, match="loop"):
            org.validate("chief", "qa", self.ROWS)       # qa -> eng -> chief

    def test_a_bot_not_created_yet_can_go_anywhere_that_exists(self):
        assert org.validate("", "eng", self.ROWS) == "eng"


class TestOnlyAPersonDrawsTheLine:
    EXISTING = {"agentId": "eng", "status": "active"}

    def test_an_agent_cannot_change_who_anyone_reports_to(self):
        actor = A.Actor(user_id="u", org_id="org-1", agent_id="chief")
        with pytest.raises(A.Escalation):
            A.plan_update(self.EXISTING, {"reportsTo": "chief"}, actor)
        # ...including its own line, or a peer's: the same rule grants and budgets follow.
        with pytest.raises(A.Escalation):
            A.plan_update({"agentId": "chief", "status": "active"}, {"reportsTo": "owner"}, actor)

    def test_a_person_can_and_it_is_audited_as_its_own_event(self):
        actor = A.Actor(user_id="owner-a", org_id="org-1")
        changes, events = A.plan_update(self.EXISTING, {"reportsTo": "chief"}, actor)
        assert changes == {"reportsTo": "chief"}
        assert [e["action"] for e in events] == ["agent.reporting_changed"]
        assert events[0]["after"] == {"reportsTo": "chief"}

    def test_it_cannot_be_set_to_itself_by_anyone(self):
        actor = A.Actor(user_id="owner-a", org_id="org-1")
        with pytest.raises(ValidationError, match="itself"):
            A.plan_update(self.EXISTING, {"reportsTo": "eng"}, actor)


class TestThroughTheApi:
    def make(self, name, **extra):
        status, created = call("POST", "/agents", {"name": name, "role": "Does the work", **extra})
        assert status in (201, 202), created
        return created

    def chief(self):
        return self.make("Chief", entrypoint=True)

    def managers(self):
        status, body = call("GET", "/agents")
        assert status == 200
        return {a["agentId"]: a["managerId"] for a in body["agents"]}

    def test_a_new_bot_reports_to_chief_with_nothing_said(self, api_table):  # noqa: F811
        self.chief()
        self.make("Engineering")
        assert self.managers() == {"chief": None, "engineering": "chief"}

    def test_it_stores_nothing_when_nothing_was_chosen(self, api_table):  # noqa: F811
        self.chief()
        eng = self.make("Engineering")
        assert "reportsTo" not in eng            # default, so nothing to migrate later

    def test_starting_a_bot_under_someone_else(self, api_table):  # noqa: F811
        self.chief()
        self.make("Engineering")
        self.make("QA", reportsTo="engineering")
        assert self.managers()["qa"] == "engineering"

    def test_starting_a_bot_under_a_bot_that_does_not_exist_is_refused(self, api_table):  # noqa: F811
        self.chief()
        status, body = call("POST", "/agents", {"name": "QA", "role": "Tests", "reportsTo": "ghost"})
        assert status == 400 and "not an active Bot" in str(body)

    def test_moving_a_bot_and_moving_it_to_the_owner(self, api_table):  # noqa: F811
        self.chief()
        self.make("Engineering")
        self.make("QA")
        assert call("PATCH", "/agents/qa", {"reportsTo": "engineering"})[0] == 200
        assert self.managers()["qa"] == "engineering"
        assert call("PATCH", "/agents/qa", {"reportsTo": "owner"})[0] == 200
        assert self.managers()["qa"] is None

    def test_a_loop_is_refused_with_a_reason(self, api_table):  # noqa: F811
        self.chief()
        self.make("Engineering")
        self.make("QA", reportsTo="engineering")
        status, body = call("PATCH", "/agents/engineering", {"reportsTo": "qa"})
        assert status == 400 and "loop" in str(body)
        assert self.managers()["engineering"] == "chief"     # unchanged

    def test_archiving_a_manager_moves_their_team_up_and_off_no_chart(self, api_table):  # noqa: F811
        self.chief()
        self.make("Engineering")
        self.make("QA", reportsTo="engineering")
        assert call("DELETE", "/agents/engineering")[0] == 200
        assert self.managers() == {"chief": None, "qa": "chief"}

    def test_one_agent_reads_the_same_as_the_list(self, api_table):  # noqa: F811
        self.chief()
        self.make("Engineering")
        status, one = call("GET", "/agents/engineering")
        assert status == 200 and one["managerId"] == "chief"

    def test_a_change_leaves_an_audit_line_of_its_own(self, api_table):  # noqa: F811
        self.chief()
        self.make("Engineering")
        call("PATCH", "/agents/engineering", {"reportsTo": "owner"})
        _, one = call("GET", "/agents/engineering")
        assert "agent.reporting_changed" in [e["action"] for e in one["audit"]]


class TestWhatABotIsTold:
    """The Bot is told its place, and told what the place is *not*."""

    def store(self, api_table):  # noqa: F811
        from amazai.store import Store
        return Store("owner-a", table=api_table)

    def team(self, api_table):  # noqa: F811
        for body in ({"name": "Chief", "entrypoint": True},
                     {"name": "Engineering", "role": "Builds"},
                     {"name": "Ops", "role": "Runs it", "reportsTo": "owner"}):
            call("POST", "/agents", {"role": "Does the work", **body})
        return self.store(api_table)

    def test_a_bot_is_told_who_it_reports_to(self, api_table):  # noqa: F811
        store = self.team(api_table)
        note = orch._reporting_note(store, store.get(K.agent_pk("engineering"), "META"))
        assert "You report to Chief." in note

    def test_a_manager_is_told_who_reports_to_it(self, api_table):  # noqa: F811
        store = self.team(api_table)
        note = orch._reporting_note(store, store.get(K.agent_pk("chief"), "META"))
        assert "You report directly to the operator." in note
        assert "Reporting to you: Engineering." in note      # Ops chose the owner, not Chief

    def test_it_says_seniority_is_not_authority(self, api_table):  # noqa: F811
        store = self.team(api_table)
        note = orch._reporting_note(store, store.get(K.agent_pk("chief"), "META"))
        assert "changes nothing about what anyone may do" in note
        assert "no Bot approves another Bot's actions" in note
