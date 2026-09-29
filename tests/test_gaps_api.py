"""The routes the console needed and the control plane did not serve.

Read state, routines, artifacts and settings were all described by the
interface long before anything answered them. Each test here is a claim the
console now relies on; if one fails, a screen is showing something the server
does not actually say.
"""

import json

import pytest

from amazai import artifacts, identity, keys as K, settings as S
from amazai.store import Store, now_iso

import handlers.api as api


def event(method, path, body=None, *, qs=None, sub="owner-a"):
    return {
        "requestContext": {
            "http": {"method": method},
            "authorizer": {"jwt": {"claims": {"sub": sub, "custom:orgId": "org-1"}}},
        },
        "rawPath": path,
        "headers": {},
        "queryStringParameters": qs,
        "body": json.dumps(body) if body is not None else None,
    }


def call(method, path, body=None, **kw):
    resp = api.handler(event(method, path, body, **kw), None)
    return resp["statusCode"], json.loads(resp["body"]) if resp.get("body") else None


#: What `_schedule` was asked to put, in order, for the test that is looking.
scheduled: list[dict] = []


@pytest.fixture
def api_table(table, monkeypatch):
    """Same wiring the other route tests use: the handler builds its own
    Store, so point the name it reaches for at moto's table, and stub the
    harness call so a created agent lands active without AWS."""
    monkeypatch.setattr(api, "_provision_harness", lambda store, agent: store.update(
        K.agent_pk(store.owner_id, agent["agentId"]), "META",
        {"harnessArn": "arn:aws:bedrock-agentcore:us-west-2:1:harness/x",
         "status": "active", "state": "active"}))
    monkeypatch.setattr(api, "Store", lambda owner_id: Store(owner_id, table=table))
    # EventBridge Scheduler is the one call the routine path makes to a
    # service. Recorded rather than made, so the record's rules are testable
    # without an account -- the same arrangement as the harness above.
    scheduled.clear()
    monkeypatch.setattr(api, "_schedule",
                        lambda store, routine: scheduled.append(routine))

    def principal_from_event(evt):
        claims = (((evt.get("requestContext") or {}).get("authorizer") or {})
                  .get("jwt") or {}).get("claims") or {}
        return identity.Principal(
            user_id=claims.get("sub", "owner-a"), email_verified=True)
    monkeypatch.setattr(api.identity, "principal_from_event", principal_from_event)
    return table


@pytest.fixture()
def agent(api_table):
    status, created = call("POST", "/agents", {
        "name": "Engineering", "role": "Builds and diagnoses",
    })
    assert status in (201, 202), created
    return created


# --- read state -------------------------------------------------------------

def test_a_thread_with_no_marker_reads_as_unread(api_table):
    status, thread = call("POST", "/threads", {"title": "Ship it"})
    assert status == 201

    status, listed = call("GET", "/threads")
    row = next(t for t in listed["threads"] if t["threadId"] == thread["threadId"])
    # Never opened, so there is activity nobody has seen. The opposite default
    # would mean a conversation that arrived overnight looked attended to.
    assert row["unread"] is True
    assert row["readAt"] is None


def test_marking_read_clears_unread(api_table):
    _, thread = call("POST", "/threads", {"title": "Ship it"})
    status, marked = call("POST", f"/threads/{thread['threadId']}/read")
    assert status == 200

    _, listed = call("GET", "/threads")
    row = next(t for t in listed["threads"] if t["threadId"] == thread["threadId"])
    assert row["unread"] is False
    assert row["readAt"] == marked["readAt"]


def test_activity_after_the_marker_makes_it_unread_again(api_table):
    _, thread = call("POST", "/threads", {"title": "Ship it"})
    call("POST", f"/threads/{thread['threadId']}/read")

    # What a run does when it writes into a thread nobody is looking at.
    # An explicit later stamp rather than now_iso(): timestamps are
    # second-resolution, so a wall-clock write can land in the same second as
    # the marker and the test would pass or fail on timing rather than on the
    # rule it is checking.
    store = Store("owner-a")
    store.update(K.thread_pk(store.owner_id, thread["threadId"]), "META",
                 {"lastActivity": "2099-01-01T00:00:00Z"})

    _, listed = call("GET", "/threads")
    row = next(t for t in listed["threads"] if t["threadId"] == thread["threadId"])
    assert row["unread"] is True


def test_marking_an_unknown_thread_read_is_not_found(api_table):
    status, _ = call("POST", "/threads/th_nope/read")
    assert status == 404


# --- routines ---------------------------------------------------------------

def test_a_routine_is_written_in_the_shape_the_worker_reads(api_table, agent):
    status, routine = call("POST", "/routines", {
        "name": "Morning briefing", "agentId": agent["agentId"],
        "prompt": "Summarise anything that changed overnight.",
        "trigger": {"type": "schedule", "expression": "cron(0 13 * * ? *)"},
    })
    assert status == 201, routine

    # handlers/routine.py reads exactly these. A record missing any of them
    # fires into an exception rather than a run.
    for field in ("agentId", "prompt", "enabled", "trigger", "limits", "threadId"):
        assert field in routine
    assert routine["enabled"] is True
    assert routine["threadId"] is None
    assert routine["lastRun"] is None


def test_a_routine_for_an_agent_that_is_not_ours_is_not_found(api_table):
    status, _ = call("POST", "/routines", {
        "name": "Morning briefing", "agentId": "someone-elses",
        "prompt": "Do a thing.", "trigger": {"type": "manual"},
    })
    assert status == 404


def test_a_routine_may_not_fire_more_often_than_the_floor(api_table, agent):
    status, body = call("POST", "/routines", {
        "name": "Too eager", "agentId": agent["agentId"], "prompt": "Check.",
        "trigger": {"type": "schedule", "expression": "rate(1 minute)"},
    })
    assert status == 400
    assert "5 minutes" in body["detail"]


def test_a_schedule_trigger_without_an_expression_is_refused(api_table, agent):
    status, body = call("POST", "/routines", {
        "name": "Nameless", "agentId": agent["agentId"], "prompt": "Check.",
        "trigger": {"type": "schedule"},
    })
    assert status == 400
    assert "expression" in body["detail"]


def test_last_run_cannot_be_written_by_a_caller(api_table, agent):
    _, routine = call("POST", "/routines", {
        "name": "Briefing", "agentId": agent["agentId"], "prompt": "Check.",
        "trigger": {"type": "manual"},
    })
    # A caller who could set this could make a routine look like it had run.
    status, body = call("PATCH", f"/routines/{routine['routineId']}",
                        {"lastRun": {"runId": "run_fake", "status": "completed"}})
    assert status == 400
    assert "lastRun" in body["detail"]


def test_deleting_a_routine_disables_it_rather_than_removing_it(api_table, agent):
    _, routine = call("POST", "/routines", {
        "name": "Briefing", "agentId": agent["agentId"], "prompt": "Check.",
        "trigger": {"type": "manual"},
    })
    status, archived = call("DELETE", f"/routines/{routine['routineId']}")
    assert status == 200
    assert archived["enabled"] is False
    assert archived["status"] == "archived"

    # Still readable: its runs and their evidence point at it.
    status, _ = call("GET", f"/routines/{routine['routineId']}")
    assert status == 200


def test_a_disabled_routine_is_still_listed(api_table, agent):
    _, routine = call("POST", "/routines", {
        "name": "Briefing", "agentId": agent["agentId"], "prompt": "Check.",
        "trigger": {"type": "manual"},
    })
    call("DELETE", f"/routines/{routine['routineId']}")
    _, listed = call("GET", "/routines")
    assert [r["routineId"] for r in listed["routines"]] == [routine["routineId"]]


def test_creating_a_routine_schedules_it(api_table, agent):
    call("PATCH", f"/agents/{agent['agentId']}", {"timezone": "America/Los_Angeles"})
    _, routine = call("POST", "/routines", {
        "name": "Briefing", "agentId": agent["agentId"], "prompt": "Check.",
        "trigger": {"type": "schedule", "expression": "cron(0 9 * * ? *)"},
    })
    assert [r["routineId"] for r in scheduled] == [routine["routineId"]]
    # "Every weekday at 9" has to mean nine where the person is, so the zone
    # travels with the schedule rather than defaulting to UTC.
    assert scheduled[-1]["timezone"] == "America/Los_Angeles"


def test_a_routine_whose_schedule_is_refused_leaves_nothing_behind(api_table, agent, monkeypatch):
    def boom(store, routine):
        raise RuntimeError("ValidationException: unknown timezone")
    monkeypatch.setattr(api, "_schedule", boom)

    status, body = call("POST", "/routines", {
        "name": "Briefing", "agentId": agent["agentId"], "prompt": "Check.",
        "trigger": {"type": "schedule", "expression": "cron(0 9 * * ? *)"},
    })
    assert status == 502
    assert body["error"] == "schedule_failed"
    # Otherwise it sits in the list looking armed and never fires.
    _, listed = call("GET", "/routines")
    assert listed["routines"] == []


def test_disabling_a_routine_puts_its_schedule_in_step(api_table, agent):
    _, routine = call("POST", "/routines", {
        "name": "Briefing", "agentId": agent["agentId"], "prompt": "Check.",
        "trigger": {"type": "schedule", "expression": "cron(0 9 * * ? *)"},
    })
    call("DELETE", f"/routines/{routine['routineId']}")
    # A record that survives with a schedule still armed keeps firing work
    # the owner switched off.
    assert scheduled[-1]["enabled"] is False
    assert scheduled[-1]["status"] == "archived"


def test_editing_the_expression_reschedules(api_table, agent):
    _, routine = call("POST", "/routines", {
        "name": "Briefing", "agentId": agent["agentId"], "prompt": "Check.",
        "trigger": {"type": "schedule", "expression": "cron(0 9 * * ? *)"},
    })
    call("PATCH", f"/routines/{routine['routineId']}",
         {"trigger": {"type": "schedule", "expression": "cron(0 17 * * ? *)"}})
    # Recorded but not rescheduled would mean the console shows one time and
    # the routine fires at another.
    assert scheduled[-1]["trigger"]["expression"] == "cron(0 17 * * ? *)"


# --- files ------------------------------------------------------------------
#
# `/artifacts` used to scan the evidence bucket's whole prefix and infer a
# file's existence from an S3 object listing alone. It is now backed by a
# real `Artifact` DynamoDB row (`amazai/artifacts.py`) -- these exercise the
# route against real rows rather than a faked bucket listing.

def test_only_real_artifacts_are_listed_not_sealed_runs(api_table, agent, monkeypatch):
    monkeypatch.setenv("EVIDENCE_BUCKET", "evidence-test")
    store = Store("owner-a")
    base = {"entity": "Run", "gsi1pk": "RUNS", "agentId": agent["agentId"]}
    store.put({**base, "pk": K.run_pk("run_sealed"), "sk": "META", "gsi1sk": "2026-01-02",
               "runId": "run_sealed", "state": "completed", "endedAt": "2026-01-02",
               "evidenceKey": "runs/run_sealed/manifest.json", "summary": "Shipped"})
    store.put({**base, "pk": K.run_pk("run_open"), "sk": "META", "gsi1sk": "2026-01-03",
               "runId": "run_open", "state": "running", "evidenceKey": None})
    # bucket="" skips the S3 put (nothing to mock here) while still writing
    # the metadata row the route actually reads.
    artifacts.create_from_content(store, run_id="run_sealed", name="launch-plan.md",
                                  content="x", bucket="")
    artifacts.create_from_content(store, run_id="run_open", name="draft.md",
                                  content="x", bucket="")

    status, body = call("GET", "/artifacts")
    assert status == 200
    # Two artifacts, not the two Run rows also in the table.
    assert sorted(a["name"] for a in body["artifacts"]) == ["draft.md", "launch-plan.md"]
    match = next(a for a in body["artifacts"] if a["name"] == "launch-plan.md")
    assert match["downloadUrl"] and "launch-plan.md" in match["downloadUrl"]


def test_files_are_newest_first(api_table, agent, monkeypatch):
    monkeypatch.setenv("EVIDENCE_BUCKET", "evidence-test")
    store = Store("owner-a")
    for run_id, ended in (("run_sealed", "2026-01-01"), ("run_open", "2026-03-01")):
        store.put({"pk": K.run_pk(run_id), "sk": "META", "entity": "Run",
                   "gsi1pk": "RUNS", "gsi1sk": ended, "runId": run_id,
                   "agentId": agent["agentId"], "state": "completed",
                   "endedAt": ended, "evidenceKey": f"runs/{run_id}/manifest.json"})
    # `now_iso()` is second-precision, so two creations in the same test can
    # tie on both `createdAt` (set in artifacts.py) and `updatedAt` (set
    # again inside Store.put) -- pin both modules' clocks explicitly rather
    # than relying on wall-clock gaps.
    import amazai.store as store_module
    monkeypatch.setattr(artifacts, "now_iso", lambda: "2026-01-01T00:00:00Z")
    monkeypatch.setattr(store_module, "now_iso", lambda: "2026-01-01T00:00:00Z")
    artifacts.create_from_content(store, run_id="run_sealed", name="launch-plan.md",
                                  content="x", bucket="")
    monkeypatch.setattr(artifacts, "now_iso", lambda: "2026-03-01T00:00:00Z")
    monkeypatch.setattr(store_module, "now_iso", lambda: "2026-03-01T00:00:00Z")
    artifacts.create_from_content(store, run_id="run_open", name="draft.md",
                                  content="x", bucket="")
    _, body = call("GET", "/artifacts")
    assert [a["name"] for a in body["artifacts"]] == ["draft.md", "launch-plan.md"]


# --- settings ---------------------------------------------------------------

def test_settings_answer_with_defaults_before_anything_is_written(api_table):
    status, body = call("GET", "/settings")
    assert status == 200
    assert body["legalAcceptedVersion"] is None
    assert body["legalAcceptedAt"] is None
    assert body["notifications"] == {"completion": True, "inputNeeded": True,
                                     "failure": True}
    assert body["theme"] == "dark"   # the app's default; "system" is a choice, not a default


def test_settings_record_versioned_legal_acceptance_once_per_version(api_table):
    status, first = call("PUT", "/settings", {
        "legalAcceptance": {"version": "2026.09", "accepted": True},
    })
    assert status == 200
    assert first["legalAcceptedVersion"] == "2026.09"
    assert first["legalAcceptedAt"]

    _, same = call("PUT", "/settings", {
        "legalAcceptance": {"version": "2026.09", "accepted": True},
    })
    assert same["legalAcceptedAt"] == first["legalAcceptedAt"]

    assert call("PUT", "/settings", {
        "legalAcceptance": {"version": "2026.09", "accepted": False},
    })[0] == 400


def test_a_partial_write_leaves_the_rest_alone(api_table):
    call("PUT", "/settings", {"theme": "dark"})
    status, body = call("PUT", "/settings", {"notifications": {"failure": False}})
    assert status == 200
    # A console that only renders the notification group must not reset a
    # theme it never showed.
    assert body["theme"] == "dark"
    assert body["notifications"]["failure"] is False
    assert body["notifications"]["completion"] is True


def test_approval_is_not_a_notification_that_can_be_switched_off(api_table):
    status, body = call("PUT", "/settings", {"notifications": {"approval": False}})
    # The gate is a question the run cannot proceed without. Refused by name
    # rather than dropped, so the caller learns why.
    assert status == 400
    assert "approval" in body["detail"]


def test_an_unknown_theme_is_refused(api_table):
    status, _ = call("PUT", "/settings", {"theme": "neon"})
    assert status == 400


# --- agent schedule ---------------------------------------------------------

def test_an_agent_carries_a_timezone_and_working_hours(api_table, agent):
    status, updated = call("PATCH", f"/agents/{agent['agentId']}", {
        "timezone": "America/Los_Angeles",
        "workingHours": {"start": "09:00", "end": "17:30", "days": [0, 1, 2, 3, 4]},
    })
    assert status == 200, updated
    assert updated["timezone"] == "America/Los_Angeles"
    assert updated["workingHours"]["days"] == [0, 1, 2, 3, 4]


def test_working_hours_can_be_cleared_back_to_always_available(api_table, agent):
    call("PATCH", f"/agents/{agent['agentId']}",
         {"workingHours": {"start": "09:00", "end": "17:00"}})
    status, updated = call("PATCH", f"/agents/{agent['agentId']}", {"workingHours": None})
    assert status == 200
    # Distinct from 00:00-23:59, and the only way to undo a window.
    assert updated["workingHours"] is None


def test_a_malformed_clock_is_refused(api_table, agent):
    status, body = call("PATCH", f"/agents/{agent['agentId']}",
                        {"workingHours": {"start": "9am", "end": "17:00"}})
    assert status == 400
    assert "HH:MM" in body["detail"]


def test_a_bare_word_is_not_a_timezone(api_table, agent):
    status, _ = call("PATCH", f"/agents/{agent['agentId']}", {"timezone": "Pacific"})
    assert status == 400


# --- first-run setup --------------------------------------------------------
#
# Setup used to be recorded in `localStorage['amazai.onboarded']`, which is a
# fact about a browser rather than about an account: a second machine, a
# private window or a cleared cache put an owner who had been here for months
# back through setup. These assert that the account is the thing that knows.

def test_setup_is_recorded_on_the_account_not_the_browser(api_table):
    status, body = call("GET", "/settings")
    assert status == 200
    # Before setup, and answered rather than 404 -- the console's guard reads
    # this on every first paint.
    assert body["onboardedAt"] is None
    assert body["workspaceName"] is None

    status, body = call("PUT", "/settings",
                        {"workspaceName": "Jaylen's workspace", "onboarded": True})
    assert status == 200, body
    assert body["workspaceName"] == "Jaylen's workspace"
    assert body["onboardedAt"]

    # A different browser is a different device, not a different account.
    _, fresh = call("GET", "/settings")
    assert fresh["onboardedAt"] == body["onboardedAt"]


def test_finishing_setup_twice_does_not_move_the_date_it_happened(api_table, monkeypatch):
    _, first = call("PUT", "/settings", {"onboarded": True})
    # The clock is moved rather than trusted to differ: `now_iso` is only
    # accurate to the second, so two writes in the same second agree by
    # accident and the assertion passes whether or not the stamp is held.
    monkeypatch.setattr(S, "now_iso", lambda: "2030-01-01T00:00:00Z")
    _, second = call("PUT", "/settings", {"onboarded": True,
                                          "workspaceName": "Renamed"})
    assert second["onboardedAt"] == first["onboardedAt"]
    # It is the stamp that is held, not the call that is dropped: everything
    # else in the same write still lands.
    assert second["workspaceName"] == "Renamed"


def test_setup_cannot_be_unset_by_asserting_it_false(api_table):
    call("PUT", "/settings", {"onboarded": True})
    status, body = call("PUT", "/settings", {"onboarded": False})
    # Named rather than ignored: a client that thinks it can clear this is
    # wrong about where setup lives, and should be told so.
    assert status == 400
    assert "onboarded" in body["detail"]
    _, after = call("GET", "/settings")
    assert after["onboardedAt"]


def test_a_client_does_not_get_to_say_when_setup_happened(api_table):
    # `onboardedAt` is output only. The write key is the assertion, and the
    # server stamps the time, so a clock-skewed browser cannot backdate an
    # account.
    status, _ = call("PUT", "/settings", {"onboardedAt": "2001-01-01T00:00:00Z"})
    assert status == 400


def test_renaming_the_workspace_leaves_the_setup_record_alone(api_table):
    _, done = call("PUT", "/settings", {"workspaceName": "First", "onboarded": True})
    _, renamed = call("PUT", "/settings", {"workspaceName": "Second"})
    assert renamed["workspaceName"] == "Second"
    assert renamed["onboardedAt"] == done["onboardedAt"]


def test_a_workspace_name_has_to_be_one_the_setup_form_could_have_produced(api_table):
    # The setup input is `maxLength={60}` and will not advance under two
    # characters. The server holds the same bounds rather than trusting them.
    assert call("PUT", "/settings", {"workspaceName": "x"})[0] == 400
    assert call("PUT", "/settings", {"workspaceName": "x" * 61})[0] == 400
    assert call("PUT", "/settings", {"workspaceName": "  ok  "})[1]["workspaceName"] == "ok"
    # Cleared, which is what the settings row does with an emptied field.
    assert call("PUT", "/settings", {"workspaceName": None})[1]["workspaceName"] is None


def test_the_payload_first_run_setup_sends_is_one_the_api_accepts(api_table):
    """Setup asks for four things -- a name, a job, a shape and a colour --
    and posts exactly those. Everything the create form also offers (tier,
    budget, grants, working style) is deliberately absent, because asking a
    new owner to set a monthly ceiling before they have seen the product is
    how setup becomes a form. If the API grew a required field, setup would
    be the last place to find out."""
    status, agent = call("POST", "/agents", {
        "name": "Pell",
        "role": "Watches production and investigates alarms",
        "avatar": {"shape": "pebble", "color": "#2f6fe4"},
    })
    assert status in (200, 201), agent
    assert agent["name"] == "Pell"
    assert agent["avatar"] == {"shape": "pebble", "color": "#2f6fe4"}
