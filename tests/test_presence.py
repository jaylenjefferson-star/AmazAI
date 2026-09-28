"""Presence is derived from durable RUN#/TASK# rows, not the live socket.

These exercise `presence.derive` over rows created through the real helpers
(`runs.create`/`runs.advance`) so the mapping is tested against the same state
machine the runtime drives, and prove the derivation writes nothing.
"""

import copy

import pytest

from amazai import keys as K, presence, runs
from amazai.states import RunState


def _seat_agent(store, agent_id, name, status="active"):
    """A minimal seated Agent row -- just the fields `presence.assemble` reads.
    The gsi1pk is what puts it in the AGENTS listing `derive` queries."""
    return store.put({
        "pk": K.agent_pk(store.owner_id, agent_id), "sk": "META",
        "entity": "Agent", "agentId": agent_id, "name": name,
        "gsi1pk": "AGENTS", "gsi1sk": name,
        "status": status,
    })


def _run_in_state(store, agent_id, state):
    """A run walked to `state` through the real transition validator, so the
    row looks exactly like one the runtime produced."""
    run = runs.create(store, agent_id=agent_id, thread_id=f"th-{agent_id}",
                      goal="do the thing")
    if state == RunState.QUEUED:
        return run
    run = runs.advance(store, run, RunState.PLANNING)
    if state == RunState.PLANNING:
        return run
    run = runs.advance(store, run, RunState.EXECUTING)
    if state == RunState.EXECUTING:
        return run
    return runs.advance(store, run, state)


def _by_agent(snapshot):
    return {p["agentId"]: p for p in snapshot}


def test_executing_run_reads_as_thinking(store):
    _seat_agent(store, "eng", "Engineering")
    _run_in_state(store, "eng", RunState.EXECUTING)

    snapshot = _by_agent(presence.derive(store))
    assert snapshot["eng"]["state"] == "thinking"
    assert snapshot["eng"]["name"] == "Engineering"
    assert snapshot["eng"]["threadId"] == "th-eng"


def test_awaiting_approval_reads_as_needs_approval(store):
    _seat_agent(store, "ops", "Operations")
    _run_in_state(store, "ops", RunState.AWAITING_APPROVAL)

    snapshot = _by_agent(presence.derive(store))
    assert snapshot["ops"]["state"] == "needs_approval"


def test_awaiting_connector_reads_as_waiting(store):
    _seat_agent(store, "conn", "Connector Bot")
    _run_in_state(store, "conn", RunState.AWAITING_CONNECTOR)

    snapshot = _by_agent(presence.derive(store))
    assert snapshot["conn"]["state"] == "waiting"


def test_only_terminal_runs_read_as_idle_and_are_omitted(store):
    _seat_agent(store, "done", "Finished Bot")
    _run_in_state(store, "done", RunState.COMPLETED)

    snapshot = presence.derive(store)
    # Idle is absence: a Bot with only a finished run is not surfaced.
    assert "done" not in _by_agent(snapshot)


def test_most_recent_non_terminal_run_wins(store):
    """An older terminal run and a newer live one: presence follows the live
    one, not whichever the listing happened to return first."""
    _seat_agent(store, "eng", "Engineering")
    _run_in_state(store, "eng", RunState.COMPLETED)
    live = _run_in_state(store, "eng", RunState.EXECUTING)

    snapshot = _by_agent(presence.derive(store))
    assert snapshot["eng"]["state"] == "thinking"
    assert snapshot["eng"]["runId"] == live["runId"]


def test_coordinator_of_open_task_waits_on_teammates(store):
    _seat_agent(store, "chief", "Chief")
    # Chief itself has no live run, but coordinates an open task with two
    # outstanding children -- it should read as waiting on its teammates rather
    # than idle-and-absent.
    store.put({
        "pk": K.task_pk("task-1"), "sk": "META", "entity": "Task", "taskId": "task-1",
        "gsi1pk": "TASKS", "gsi1sk": K.tasks_gsi1_sk("open", "2026-01-01T00:00:00Z"),
        "coordinatorAgentId": "chief", "status": "open", "pendingChildren": 2,
    })

    snapshot = _by_agent(presence.derive(store))
    assert snapshot["chief"]["state"] == "waiting"
    assert "2 teammates" in snapshot["chief"]["action"]


def test_closed_task_does_not_make_a_coordinator_wait(store):
    _seat_agent(store, "chief", "Chief")
    store.put({
        "pk": K.task_pk("task-2"), "sk": "META", "entity": "Task", "taskId": "task-2",
        "gsi1pk": "TASKS", "gsi1sk": K.tasks_gsi1_sk("closed", "2026-01-01T00:00:00Z"),
        "coordinatorAgentId": "chief", "status": "closed", "pendingChildren": 0,
    })

    assert "chief" not in _by_agent(presence.derive(store))


def test_unseated_agent_is_not_surfaced(store):
    _seat_agent(store, "old", "Archived Bot", status="archived")
    # Even with a live run, a Bot that holds no seat (archived/failed) is not
    # part of the org someone is watching for motion.
    _run_in_state(store, "old", RunState.EXECUTING)

    assert "old" not in _by_agent(presence.derive(store))


def test_derive_performs_no_writes(store):
    """The whole point: a presence read must never mutate the rows it reads."""
    _seat_agent(store, "eng", "Engineering")
    _run_in_state(store, "eng", RunState.EXECUTING)

    before = sorted(store.query_index("gsi1", "gsi1pk", "RUNS", limit=1000),
                    key=lambda r: r["pk"])
    before_copy = copy.deepcopy(before)

    presence.derive(store)

    after = sorted(store.query_index("gsi1", "gsi1pk", "RUNS", limit=1000),
                   key=lambda r: r["pk"])
    # Byte-for-byte identical: same rows, same values, nothing added or changed.
    assert after == before_copy


def test_assemble_is_pure_over_rows():
    """`assemble` needs no store at all -- it is a function of the rows."""
    agents = [{"agentId": "eng", "name": "Engineering", "status": "active"}]
    runs_rows = [{"runId": "run_1", "agentId": "eng", "threadId": "th-eng",
                  "state": "EXECUTING", "startedAt": "2026-01-01T00:00:00Z"}]
    snapshot = presence.assemble(agents, runs_rows, [])
    assert snapshot == [{
        "agentId": "eng", "name": "Engineering", "state": "thinking",
        "action": "", "runId": "run_1", "threadId": "th-eng",
        "since": "2026-01-01T00:00:00Z",
    }]


# The full run-state -> (presence state, action line) table, kept word-for-word
# in step with `web/src/presence.js` RUN_STATES. A reload seeds the live store
# from this backend derivation, so a divergence in either the state bucket OR
# the action text would show up as a Bot that reads one way over the socket and
# another after refresh. Asserting the whole tuple (not just the bucket) is what
# guards the canonical activity vocabulary against silent drift on one side.
_FRONTEND_RUN_STATES = {
    "QUEUED":             ("thinking", "Getting started"),
    "PLANNING":           ("thinking", "Planning"),
    "EXECUTING":          ("thinking", ""),
    "RETRYING":           ("working", "Retrying"),
    "AWAITING_APPROVAL":  ("needs_approval", "Waiting for your approval"),
    "AWAITING_INPUT":     ("needs_approval", "Waiting for your answer"),
    "AWAITING_LOGIN":     ("needs_approval", "Waiting for you to sign in"),
    "AWAITING_CONNECTOR": ("waiting", "Waiting on a connector"),
    "SUSPENDED":          ("waiting", "Paused"),
    "CANCELLING":         ("waiting", "Stopping"),
}


@pytest.mark.parametrize("run_state,expected", list(_FRONTEND_RUN_STATES.items()))
def test_state_mapping_matches_frontend_semantics(run_state, expected):
    # The whole tuple: presence bucket AND action line agree word-for-word with
    # the frontend RUN_STATES, so the socket and a reload never disagree.
    assert presence.state_for_run(run_state) == expected


def test_coordinator_overlay_uses_the_teammate_vocabulary(store):
    """The 'waiting on teammate' vocabulary is shared: a coordinator with one
    outstanding child reads 'teammate', more than one reads 'teammates', and
    the state overlays to `waiting` -- the same word the frontend STATES verb
    ('waiting on a teammate') and RUN_STATES use for waiting on the org."""
    _seat_agent(store, "solo", "Solo")
    store.put({
        "pk": K.task_pk("task-solo"), "sk": "META", "entity": "Task", "taskId": "task-solo",
        "gsi1pk": "TASKS", "gsi1sk": K.tasks_gsi1_sk("open", "2026-01-01T00:00:00Z"),
        "coordinatorAgentId": "solo", "status": "open", "pendingChildren": 1,
    })

    snapshot = _by_agent(presence.derive(store))
    assert snapshot["solo"]["state"] == "waiting"
    assert snapshot["solo"]["action"] == "Waiting on 1 teammate"


# --- the room coordination view -------------------------------------------
# A room's coordination state -- who owns the current stage, working/waiting/
# needs-approval, whether a handoff occurred, whether an artifact was produced
# -- read back from durable RUN#/HOFF#/artifact rows, never a live model. These
# exercise `presence.room_coordination` over rows the real helpers produced so
# the derivation is tested against the state machine the runtime drives.

from amazai import artifacts as AR, handoffs  # noqa: E402


def _room(store, thread_id, agent_ids, title="Eng Ops"):
    store.put({
        "pk": K.thread_pk(store.owner_id, thread_id), "sk": "META",
        "entity": "Thread", "threadId": thread_id, "kind": "room",
        "gsi1pk": "THREADS", "gsi1sk": "2026-01-01T00:00:00Z",
        "title": title, "agentIds": list(agent_ids), "status": "active",
    })


def _run_on_thread(store, agent_id, thread_id, state):
    run = runs.create(store, agent_id=agent_id, thread_id=thread_id, goal="do the thing")
    if state == RunState.QUEUED:
        return run
    run = runs.advance(store, run, RunState.PLANNING)
    if state == RunState.PLANNING:
        return run
    run = runs.advance(store, run, RunState.EXECUTING)
    if state == RunState.EXECUTING:
        return run
    return runs.advance(store, run, state)


def test_room_view_names_the_stage_owner_and_member_states(store):
    _seat_agent(store, "eng", "Engineering")
    _seat_agent(store, "chief", "Chief")
    _room(store, "th-room", ["eng", "chief"])
    _run_on_thread(store, "eng", "th-room", RunState.EXECUTING)
    _run_on_thread(store, "chief", "th-room", RunState.AWAITING_APPROVAL)

    view = presence.room_coordination(store, "th-room")
    by_agent = {m["agentId"]: m for m in view["members"]}
    # Both members are surfaced, each with the same bucket its own Companion reads.
    assert by_agent["eng"]["state"] == "thinking"
    assert by_agent["chief"]["state"] == "needs_approval"
    # The one actively in a turn owns the stage; the one parked on an approval
    # does not.
    assert view["stageOwnerAgentId"] == "eng"
    assert view["working"] is True
    assert view["needsApproval"] is True


def test_room_view_reports_a_handoff_occurred(store):
    _seat_agent(store, "chief", "Chief")
    _seat_agent(store, "eng", "Engineering")
    _room(store, "th-room", ["chief", "eng"])
    run = _run_on_thread(store, "chief", "th-room", RunState.EXECUTING)
    # A proposed handoff row on a run this thread drove is a room-level fact.
    store.put({
        "pk": run["pk"], "sk": K.handoff_sk("hoff-1"), "entity": "Handoff",
        "handoffId": "hoff-1", "fromAgentId": "chief", "toAgentId": "eng",
        "status": "proposed", "goal": "take the build", "createdAt": "2026-01-01T00:01:00Z",
    })

    view = presence.room_coordination(store, "th-room")
    assert view["handoffOccurred"] is True
    assert view["artifactProduced"] is False


def test_room_view_reports_an_artifact_produced(store):
    _seat_agent(store, "eng", "Engineering")
    _room(store, "th-room", ["eng"])
    run = _run_on_thread(store, "eng", "th-room", RunState.EXECUTING)
    AR.create_from_content(store, run_id=run["runId"], name="report.md",
                           content="the deliverable", created_by_agent_id="eng")

    view = presence.room_coordination(store, "th-room")
    assert view["artifactProduced"] is True


def test_room_view_of_an_idle_room_is_empty_of_activity(store):
    _seat_agent(store, "eng", "Engineering")
    _room(store, "th-quiet", ["eng"])

    view = presence.room_coordination(store, "th-quiet")
    assert view["stageOwnerAgentId"] is None
    assert view["working"] is False and view["waiting"] is False
    assert view["needsApproval"] is False
    assert view["handoffOccurred"] is False and view["artifactProduced"] is False
    # A member with no live run still appears, drawn idle -- the room lists who
    # is in it even when nobody is moving.
    assert [m["agentId"] for m in view["members"]] == ["eng"]


def test_room_view_of_a_missing_thread_is_empty_not_an_error(store):
    view = presence.room_coordination(store, "th-nope")
    assert view["members"] == []
    assert view["stageOwnerAgentId"] is None


def test_room_view_performs_no_writes(store):
    _seat_agent(store, "eng", "Engineering")
    _room(store, "th-room", ["eng"])
    _run_on_thread(store, "eng", "th-room", RunState.EXECUTING)

    before = sorted(store.query_index("gsi1", "gsi1pk", "RUNS", limit=1000),
                    key=lambda r: r["pk"])
    before_copy = copy.deepcopy(before)

    presence.room_coordination(store, "th-room")

    after = sorted(store.query_index("gsi1", "gsi1pk", "RUNS", limit=1000),
                   key=lambda r: r["pk"])
    assert after == before_copy


def test_assemble_room_is_pure_over_rows():
    """`assemble_room` needs no store -- it is a function of the rows."""
    agents = [{"agentId": "eng", "name": "Engineering"},
              {"agentId": "chief", "name": "Chief"}]
    runs_rows = [{"runId": "run_1", "agentId": "eng", "threadId": "th",
                  "state": "EXECUTING", "startedAt": "2026-01-01T00:00:00Z"}]
    view = presence.assemble_room(agents, runs_rows, handoff_seen=True, artifact_seen=False)
    assert view["stageOwnerAgentId"] == "eng"
    assert view["working"] is True
    assert view["handoffOccurred"] is True
    assert view["artifactProduced"] is False
    assert {m["agentId"] for m in view["members"]} == {"eng", "chief"}
