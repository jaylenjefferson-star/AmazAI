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


@pytest.mark.parametrize("run_state,expected", [
    ("QUEUED", "thinking"),
    ("PLANNING", "thinking"),
    ("EXECUTING", "thinking"),
    ("RETRYING", "working"),
    ("AWAITING_APPROVAL", "needs_approval"),
    ("AWAITING_INPUT", "needs_approval"),
    ("AWAITING_LOGIN", "needs_approval"),
    ("AWAITING_CONNECTOR", "waiting"),
    ("SUSPENDED", "waiting"),
    ("CANCELLING", "waiting"),
])
def test_state_mapping_matches_frontend_semantics(run_state, expected):
    assert presence.state_for_run(run_state)[0] == expected
