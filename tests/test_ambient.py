"""Ambient signals derived from durable state.

`ambient.derive` answers "what has the org been up to lately" the same way
`presence.derive` answers "what is happening right now": a pure read over rows
the control plane already persists, never a live model and never a process kept
warm for an idle Companion. These exercise the fold over real rows and prove it
is bounded, state-derived and read-only.
"""

import copy

import pytest

from amazai import ambient, keys as K
from amazai.store import now_iso


def _task_row(store, task_id, *, status, coordinator="chief", pending=0,
              goal="ship the thing", created="2026-01-01T00:00:00Z",
              closed=None, outcome=None):
    row = {
        "pk": K.task_pk(task_id), "sk": "META", "entity": "Task", "taskId": task_id,
        "gsi1pk": "TASKS", "gsi1sk": K.tasks_gsi1_sk(status, created),
        "coordinatorAgentId": coordinator, "status": status,
        "pendingChildren": pending, "goal": goal, "createdAt": created,
        "threadId": f"th-{task_id}",
    }
    if closed:
        row["closedAt"] = closed
    if outcome:
        row["outcome"] = outcome
    return store.put(row)


def _artifact_row(store, artifact_id, *, name="report.md", created,
                  status="ready", agent="eng", artifact_type="document"):
    return store.put({
        "pk": K.artifact_pk(artifact_id), "sk": "META", "entity": "Artifact",
        "artifactId": artifact_id, "createdAt": created,
        "gsi1pk": "ARTIFACTS", "gsi1sk": K.artifacts_gsi1_sk(status, created),
        "createdByAgentId": agent, "artifactType": artifact_type,
        "name": name, "status": status, "taskId": "task-1", "threadId": "th-1",
    })


class TestAssembleIsPureOverRows:
    """`assemble` needs no store: it is a function of the rows, so it can be
    tested directly and is provably read-only."""

    def test_closed_task_becomes_a_completed_signal(self):
        out = ambient.assemble(
            [{"taskId": "t1", "status": "closed", "coordinatorAgentId": "chief",
              "goal": "ship it", "closedAt": "2026-01-02T00:00:00Z",
              "outcome": "success"}], [])
        assert len(out["completed"]) == 1
        sig = out["completed"][0]
        assert sig["kind"] == "completed"
        assert sig["taskId"] == "t1"
        assert sig["outcome"] == "success"
        assert sig["title"] == "ship it"

    def test_open_task_with_outstanding_children_becomes_a_waiting_signal(self):
        out = ambient.assemble(
            [{"taskId": "t1", "status": "open", "coordinatorAgentId": "chief",
              "pendingChildren": 3, "createdAt": "2026-01-01T00:00:00Z"}], [])
        assert out["waiting"] == [{
            "kind": "waiting", "at": "2026-01-01T00:00:00Z", "agentId": "chief",
            "taskId": "t1", "threadId": None, "pendingChildren": 3, "title": ""}]
        assert out["completed"] == []

    def test_open_task_with_no_outstanding_children_is_silent(self):
        # Nothing to wait on is not an ambient signal: it would be noise.
        out = ambient.assemble(
            [{"taskId": "t1", "status": "open", "coordinatorAgentId": "chief",
              "pendingChildren": 0}], [])
        assert out["waiting"] == []
        assert out["completed"] == []

    def test_a_waiting_task_needs_a_coordinator_to_attribute_it_to(self):
        out = ambient.assemble(
            [{"taskId": "t1", "status": "open", "pendingChildren": 2}], [])
        assert out["waiting"] == []

    def test_ready_artifact_becomes_a_produced_signal(self):
        out = ambient.assemble([], [
            {"artifactId": "a1", "status": "ready", "createdByAgentId": "eng",
             "artifactType": "document", "name": "report.md",
             "createdAt": "2026-01-03T00:00:00Z"}])
        assert len(out["produced"]) == 1
        assert out["produced"][0]["artifactId"] == "a1"
        assert out["produced"][0]["artifactType"] == "document"

    def test_an_unready_artifact_is_not_produced_yet(self):
        out = ambient.assemble([], [
            {"artifactId": "a1", "status": "pending", "createdAt": "2026-01-03"}])
        assert out["produced"] == []

    def test_signals_are_newest_first(self):
        tasks = [
            {"taskId": "old", "status": "closed", "coordinatorAgentId": "c",
             "closedAt": "2026-01-01T00:00:00Z"},
            {"taskId": "new", "status": "closed", "coordinatorAgentId": "c",
             "closedAt": "2026-03-01T00:00:00Z"},
        ]
        out = ambient.assemble(tasks, [])
        assert [s["taskId"] for s in out["completed"]] == ["new", "old"]

    def test_each_bucket_is_bounded(self):
        # A busy month cannot turn the feed into an unbounded log.
        tasks = [{"taskId": f"t{i}", "status": "closed", "coordinatorAgentId": "c",
                  "closedAt": f"2026-01-{i:02d}T00:00:00Z"} for i in range(1, 25)]
        out = ambient.assemble(tasks, [], limit=5)
        assert len(out["completed"]) == 5

    def test_title_is_truncated(self):
        out = ambient.assemble(
            [{"taskId": "t1", "status": "closed", "coordinatorAgentId": "c",
              "goal": "x" * 500, "closedAt": "2026-01-01T00:00:00Z"}], [])
        assert len(out["completed"][0]["title"]) == 200


class TestDeriveReadsDurableRows:
    def test_derive_folds_tasks_and_artifacts(self, store):
        _task_row(store, "done-1", status="closed", closed="2026-02-01T00:00:00Z",
                  outcome="success")
        _task_row(store, "open-1", status="open", pending=2)
        _artifact_row(store, "art-1", created="2026-02-02T00:00:00Z")

        out = ambient.derive(store)
        assert [s["taskId"] for s in out["completed"]] == ["done-1"]
        assert [s["taskId"] for s in out["waiting"]] == ["open-1"]
        assert [s["artifactId"] for s in out["produced"]] == ["art-1"]

    def test_derive_performs_no_writes(self, store):
        """The whole point: an ambient read must never mutate the rows it reads."""
        _task_row(store, "done-1", status="closed", closed="2026-02-01T00:00:00Z")
        _task_row(store, "open-1", status="open", pending=1)
        _artifact_row(store, "art-1", created="2026-02-02T00:00:00Z")

        def _snapshot():
            rows = store.query_index("gsi1", "gsi1pk", "TASKS", limit=1000)
            rows += store.query_index("gsi1", "gsi1pk", "ARTIFACTS", limit=1000)
            return sorted(rows, key=lambda r: r["pk"])

        before = copy.deepcopy(_snapshot())
        ambient.derive(store)
        assert _snapshot() == before

    def test_derive_is_bounded(self, store):
        for i in range(1, 20):
            _task_row(store, f"done-{i:02d}", status="closed",
                      closed=f"2026-01-{i:02d}T00:00:00Z")
        out = ambient.derive(store, limit=6)
        assert len(out["completed"]) == 6
