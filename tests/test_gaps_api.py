"""The routes the console needed and the control plane did not serve.

Read state, routines, artifacts and settings were all described by the
interface long before anything answered them. Each test here is a claim the
console now relies on; if one fails, a screen is showing something the server
does not actually say.
"""

import json

import pytest

from amazai import identity, keys as K
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
        K.agent_pk(agent["agentId"]), "META",
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
        return identity.Principal(user_id=claims.get("sub", "owner-a"))
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
    store.update(K.thread_pk(thread["threadId"]), "META",
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


# --- artifacts --------------------------------------------------------------

def test_only_runs_that_sealed_a_bundle_are_artifacts(api_table, agent):
    store = Store("owner-a")
    base = {"entity": "Run", "gsi1pk": "RUNS", "agentId": agent["agentId"]}
    store.put({**base, "pk": K.run_pk("run_sealed"), "sk": "META", "gsi1sk": "2026-01-02",
               "runId": "run_sealed", "state": "completed", "endedAt": "2026-01-02",
               "evidenceKey": "runs/run_sealed/manifest.json", "summary": "Shipped"})
    store.put({**base, "pk": K.run_pk("run_open"), "sk": "META", "gsi1sk": "2026-01-03",
               "runId": "run_open", "state": "running", "evidenceKey": None})

    status, body = call("GET", "/artifacts")
    assert status == 200
    # A run still in flight has produced nothing to point at yet.
    assert [a["runId"] for a in body["artifacts"]] == ["run_sealed"]
    assert body["artifacts"][0]["evidenceKey"] == "runs/run_sealed/manifest.json"


def test_artifacts_are_newest_first(api_table, agent):
    store = Store("owner-a")
    for run_id, ended in (("run_old", "2026-01-01"), ("run_new", "2026-03-01")):
        store.put({"pk": K.run_pk(run_id), "sk": "META", "entity": "Run",
                   "gsi1pk": "RUNS", "gsi1sk": ended, "runId": run_id,
                   "agentId": agent["agentId"], "state": "completed",
                   "endedAt": ended, "evidenceKey": f"runs/{run_id}/manifest.json"})
    _, body = call("GET", "/artifacts")
    assert [a["runId"] for a in body["artifacts"]] == ["run_new", "run_old"]


# --- settings ---------------------------------------------------------------

def test_settings_answer_with_defaults_before_anything_is_written(api_table):
    status, body = call("GET", "/settings")
    assert status == 200
    assert body["notifications"] == {"completion": True, "inputNeeded": True,
                                     "failure": True}
    assert body["theme"] == "system"


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
