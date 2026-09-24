"""Concurrency guarantees, exercised directly rather than assumed.

Most of the individual mechanisms these lean on already have their own
focused tests elsewhere (fan-in race-safety in test_handoff_continuation.py,
artifact no-collision in test_artifacts.py, session isolation in
test_task_isolation.py) -- these are the cross-cutting scenarios: the same
worker juggling unrelated jobs, several coordinators at once, two tenants at
once, and a duplicate invocation racing a real one.
"""

from __future__ import annotations

import pytest

from amazai import artifacts, handoffs, keys as K, runs
from amazai.states import RunState
from amazai.store import Conflict, NotFound, Store

import handlers.orchestrator as orch

MODEL = {"modelId": "test-model", "tier": "balanced"}
FINANCE = {"agentId": "fin", "name": "Finance", "status": "active", "state": "active",
          "model": MODEL, "budget": {"maxConcurrentRuns": 5}}
MARKETING = {"agentId": "mkt", "name": "Marketing", "status": "active", "state": "active",
            "model": MODEL, "budget": {"maxConcurrentRuns": 5}}
COO = {"agentId": "coo", "name": "Chief Operating Officer", "status": "active", "state": "active",
      "model": MODEL, "budget": {"maxConcurrentRuns": 5}}
CTO = {"agentId": "cto", "name": "Chief Technology Officer", "status": "active", "state": "active",
      "model": MODEL, "budget": {"maxConcurrentRuns": 5}}


@pytest.fixture
def agents(store):
    for a in (FINANCE, MARKETING, COO, CTO):
        store.put({"pk": K.agent_pk(store.owner_id, a["agentId"]), "sk": "META",
                  "entity": "Agent", **a})
    return store


class TestSameWorkerMultipleUnrelatedTasks:
    """A: the same custom worker receives two unrelated jobs at once."""

    def test_each_job_gets_its_own_run_session_and_artifacts(self, agents):
        coordinator_a = runs.create(agents, agent_id="coo", thread_id="dm-coo", goal="job a")
        coordinator_b = runs.create(agents, agent_id="cto", thread_id="dm-cto", goal="job b")

        h1 = orch._record_handoff(agents, coordinator_a, {"to": "fin", "goal": "task a"})
        a1 = handoffs.accept(agents, coordinator_a, h1, decided_by="system:auto-accept")
        h2 = orch._record_handoff(agents, coordinator_b, {"to": "fin", "goal": "task b"})
        a2 = handoffs.accept(agents, coordinator_b, h2, decided_by="system:auto-accept")

        # Unique runs.
        assert a1["child"]["runId"] != a2["child"]["runId"]
        # Unique sessions -- the exact thing PR #72 fixed: two tasks landing
        # on the same worker must never hash to the same AgentCore session.
        assert a1["child"]["sessionId"] != a2["child"]["sessionId"]
        assert a1["child"]["threadId"] != a2["child"]["threadId"]

        # Isolated context: neither child's own thread carries the other's brief.
        thread_a_messages = agents.query(K.thread_pk(agents.owner_id, a1["child"]["threadId"]),
                                         sk_prefix="MSG#")
        assert not any("task b" in (m.get("text") or "") for m in thread_a_messages)

        # Isolated artifacts: each job's own output lands under its own run.
        artifacts.create_from_content(agents, run_id=a1["child"]["runId"],
                                      name="a.md", content="a", bucket="")
        artifacts.create_from_content(agents, run_id=a2["child"]["runId"],
                                      name="b.md", content="b", bucket="")
        assert [x["name"] for x in artifacts.list_for_run(agents, a1["child"]["runId"])] == ["a.md"]
        assert [x["name"] for x in artifacts.list_for_run(agents, a2["child"]["runId"])] == ["b.md"]


class TestMultipleCoordinatorsConcurrently:
    """C: two independent coordinators fanning out at the same time must not
    interfere with each other's tasks, counters or artifacts."""

    def test_two_coordinators_fan_out_independently(self, agents):
        coo_run = runs.create(agents, agent_id="coo", thread_id="dm-coo", goal="coo's project")
        cto_run = runs.create(agents, agent_id="cto", thread_id="dm-cto", goal="cto's project")

        h_coo = orch._record_handoff(agents, coo_run, {"to": "fin", "goal": "coo's financial ask"})
        h_cto = orch._record_handoff(agents, cto_run, {"to": "mkt", "goal": "cto's launch ask"})
        a_coo = handoffs.accept(agents, coo_run, h_coo, decided_by="system:auto-accept")
        a_cto = handoffs.accept(agents, cto_run, h_cto, decided_by="system:auto-accept")

        task_coo = agents.get(K.task_pk(coo_run["runId"]), "META")
        task_cto = agents.get(K.task_pk(cto_run["runId"]), "META")
        assert task_coo["pendingChildren"] == 1
        assert task_cto["pendingChildren"] == 1
        assert task_coo["coordinatorAgentId"] == "coo"
        assert task_cto["coordinatorAgentId"] == "cto"

        # Settling one coordinator's child must not touch the other's counter.
        handoffs.notify_coordinator_if_child(
            agents, a_coo["child"], RunState.COMPLETED.value, "coo's model done")
        assert agents.get(K.task_pk(cto_run["runId"]), "META")["pendingChildren"] == 1

        handoffs.notify_coordinator_if_child(
            agents, a_cto["child"], RunState.COMPLETED.value, "cto's plan done")
        assert agents.get(K.task_pk(cto_run["runId"]), "META")["pendingChildren"] == 0


class TestTwoTenantsSimultaneously:
    """D: identical operations from two tenants at once, zero leakage in
    either direction -- tasks, runs and artifacts alike."""

    def test_identical_operations_do_not_cross_tenant_boundaries(self, two_stores):
        owner_a, owner_b = two_stores
        for store in (owner_a, owner_b):
            store.put({"pk": K.agent_pk(store.owner_id, "fin"), "sk": "META",
                      "entity": "Agent", **FINANCE})
            store.put({"pk": K.agent_pk(store.owner_id, "coo"), "sk": "META",
                      "entity": "Agent", **COO})

        # Interleaved, as two real concurrent requests would land.
        run_a = runs.create(owner_a, agent_id="coo", thread_id="dm-coo", goal="evaluate pricing")
        run_b = runs.create(owner_b, agent_id="coo", thread_id="dm-coo", goal="evaluate pricing")
        h_a = orch._record_handoff(owner_a, run_a, {"to": "fin", "goal": "model it"})
        h_b = orch._record_handoff(owner_b, run_b, {"to": "fin", "goal": "model it"})
        accepted_a = handoffs.accept(owner_a, run_a, h_a, decided_by="system:auto-accept")
        accepted_b = handoffs.accept(owner_b, run_b, h_b, decided_by="system:auto-accept")
        artifacts.create_from_content(owner_a, run_id=accepted_a["child"]["runId"],
                                      name="Model.xlsx", content="tenant a's numbers", bucket="")
        artifacts.create_from_content(owner_b, run_id=accepted_b["child"]["runId"],
                                      name="Model.xlsx", content="tenant b's numbers", bucket="")

        # Each tenant's task listing shows only its own task.
        tasks_a = owner_a.query_index("gsi1", "gsi1pk", "TASKS", limit=50)
        tasks_b = owner_b.query_index("gsi1", "gsi1pk", "TASKS", limit=50)
        assert {t["taskId"] for t in tasks_a} == {run_a["runId"]}
        assert {t["taskId"] for t in tasks_b} == {run_b["runId"]}

        # Tenant B cannot read tenant A's artifact by id, and vice versa,
        # even though both created a same-named file at (nearly) the same time.
        artifact_a = artifacts.list_for_run(owner_a, accepted_a["child"]["runId"])[0]
        artifact_b = artifacts.list_for_run(owner_b, accepted_b["child"]["runId"])[0]
        assert artifact_a["artifactId"] != artifact_b["artifactId"]
        with pytest.raises(NotFound):
            artifacts.get(owner_b, artifact_a["artifactId"])
        with pytest.raises(NotFound):
            artifacts.get(owner_a, artifact_b["artifactId"])


class TestDuplicateInvocationIsNotFatal:
    """H: a duplicate/racing invocation of the same run must not corrupt or
    fail work the winning invocation is legitimately still doing.

    `runs.advance`'s own docstring says a losing race means "the loser
    re-reads" -- but nothing before this caught the `Conflict` that losing
    actually raises, so it fell through to the handler's blanket exception
    catch, which called `_fail` unconditionally. A duplicate delivery could
    therefore seal a still-in-progress run as FAILED out from under the
    invocation that actually won.
    """

    def test_a_losing_race_on_the_runs_own_transition_does_not_fail_it(self, agents):
        agents.put({"pk": K.thread_pk(agents.owner_id, "dm-fin"), "sk": "META",
                   "entity": "Thread", "kind": "dm", "threadId": "dm-fin"})
        run = runs.create(agents, agent_id="fin", thread_id="dm-fin", goal="g")
        # "A" wins the race for real.
        winner = runs.advance(agents, run, RunState.PLANNING)
        runs.advance(agents, winner, RunState.EXECUTING)

        # "B" is a duplicate/racing invocation holding the same stale
        # (still-QUEUED) snapshot A started from -- exactly what a losing
        # `handler()` call sees. Confirm the mechanism really raises what
        # the winner's own advance already made true.
        with pytest.raises(Conflict):
            runs.advance(agents, run, RunState.PLANNING)

        # The fix: handler() must not treat that Conflict as this run's
        # failure. Simulate its own try/except directly.
        event = {"runId": run["runId"], "ownerId": agents.owner_id}
        try:
            orch._drive(agents, run, event)
        except Conflict:
            result = {"ok": True, "runId": run["runId"], "skipped": "lost a race"}
        else:
            pytest.fail("expected the stale snapshot's advance to raise Conflict")

        assert result["ok"] is True
        # The winner's own progress must be untouched -- still EXECUTING, not FAILED.
        current = agents.get(run["pk"], "META")
        assert current["state"] == RunState.EXECUTING.value

    def test_handler_itself_stands_down_on_a_losing_race_without_failing_the_run(
            self, agents, monkeypatch):
        agents.put({"pk": K.thread_pk(agents.owner_id, "dm-fin"), "sk": "META",
                   "entity": "Thread", "kind": "dm", "threadId": "dm-fin"})
        run = runs.create(agents, agent_id="fin", thread_id="dm-fin", goal="g")
        winner = runs.advance(agents, run, RunState.PLANNING)
        runs.advance(agents, winner, RunState.EXECUTING)

        # handler() re-fetches the run row itself; make only *that* read see
        # the stale QUEUED snapshot a slightly-earlier duplicate delivery
        # would have -- every other store.get (agent lookups, etc.) is real.
        real_get = Store.get

        def stale_for_this_run(self, pk, sk, **kw):
            if pk == run["pk"] and sk == "META":
                return dict(run)
            return real_get(self, pk, sk, **kw)
        monkeypatch.setattr(Store, "get", stale_for_this_run)

        result = orch.handler({"runId": run["runId"], "ownerId": agents.owner_id}, None)

        assert result["ok"] is True
        assert "race" in result.get("skipped", "")
        # And the winner's real progress is still intact.
        current = real_get(agents, run["pk"], "META")
        assert current["state"] == RunState.EXECUTING.value
