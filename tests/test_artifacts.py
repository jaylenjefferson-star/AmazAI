"""Durable artifacts: creation, storage, isolation, versioning, and the
handoff/fan-in paths that pass them by reference instead of by content.

`EvidenceWriter.artifact()` existed before this module and had no caller --
these tests are for the actual first working path: `artifacts.py`, the
`create_artifact`/`read_artifact` inline tools, and the `/artifacts` API
routes now backed by a real DynamoDB entity instead of an always-empty
bucket scan.
"""

from __future__ import annotations

import types

import boto3
import pytest

from amazai import artifacts, handoffs, keys as K, runs
from amazai.cost import RunCost
from amazai.evidence import EvidenceWriter
from amazai.push import Push
from amazai.states import RunState
from amazai.store import NotFound, Store

import handlers.orchestrator as orch
from tests.test_agents_api import api_table, call  # noqa: F401

BUCKET = "test-evidence-bucket"


@pytest.fixture
def s3(table):
    """A real (moto-mocked) S3 client and bucket, inside the same `mock_aws()`
    context the `table` fixture already opened."""
    client = boto3.client("s3", region_name="us-west-2")
    client.create_bucket(Bucket=BUCKET,
                         CreateBucketConfiguration={"LocationConstraint": "us-west-2"})
    return client


class RecPush(Push):
    def __init__(self):
        self.sent = []

    def send(self, payload):
        self.sent.append(payload)
        return 0


def _tool_call(name, args, tool_use_id="tu-1"):
    return types.SimpleNamespace(tool_name=name, tool_input=args, tool_use_id=tool_use_id)


AGENT = {"agentId": "fin", "name": "Finance", "status": "active",
        "budget": {"maxConcurrentRuns": 5}}


@pytest.fixture
def agents(store):
    store.put({"pk": K.agent_pk(store.owner_id, "fin"), "sk": "META", "entity": "Agent", **AGENT})
    return store


@pytest.fixture
def a_run(agents):
    return runs.create(agents, agent_id="fin", thread_id="dm-fin", goal="build a pricing model")


class TestCreateFromContent:
    def test_metadata_persists_in_dynamodb(self, agents, a_run, s3):
        row = artifacts.create_from_content(
            agents, run_id=a_run["runId"], name="Pricing Model.md",
            content="# Pricing\n\nTiers...", content_type="text/markdown",
            artifact_type="document", created_by_agent_id="fin", bucket=BUCKET, s3=s3)

        assert row["status"] == "ready"
        assert row["version"] == 1
        assert row["name"] == "Pricing Model.md"
        assert row["artifactType"] == "document"
        assert row["runId"] == a_run["runId"]
        assert row["checksum"]
        assert row["sizeBytes"] > 0

        reloaded = artifacts.get(agents, row["artifactId"])
        assert reloaded["name"] == "Pricing Model.md"

    def test_bytes_land_outside_dynamodb(self, agents, a_run, s3):
        row = artifacts.create_from_content(
            agents, run_id=a_run["runId"], name="report.txt", content="the actual content",
            bucket=BUCKET, s3=s3)

        obj = s3.get_object(Bucket=BUCKET, Key=row["storageKey"])
        assert obj["Body"].read() == b"the actual content"
        # The DynamoDB row never carries the body itself.
        assert "content" not in row and "body" not in row

    def test_persists_after_the_run_that_produced_it_ends(self, agents, a_run, s3):
        row = artifacts.create_from_content(
            agents, run_id=a_run["runId"], name="report.txt", content="x", bucket=BUCKET, s3=s3)
        runs.advance(agents, a_run, RunState.COMPLETED)

        assert artifacts.get(agents, row["artifactId"])["status"] == "ready"

    def test_oversized_content_is_refused(self, agents, a_run, s3):
        with pytest.raises(artifacts.ValidationError):
            artifacts.create_from_content(
                agents, run_id=a_run["runId"], name="huge.txt",
                content="x" * (artifacts.MAX_INLINE_CONTENT_BYTES + 1), bucket=BUCKET, s3=s3)

    def test_two_concurrent_runs_of_the_same_agent_do_not_collide(self, agents, s3):
        """Scenario 5: Finance runs Job A and Job B at once, each producing a
        same-shaped file. Different runIds must mean different storage keys
        and different artifact ids regardless of what either names its file."""
        run_a = runs.create(agents, agent_id="fin", thread_id="th-a", goal="job a")
        run_b = runs.create(agents, agent_id="fin", thread_id="th-b", goal="job b")

        a = artifacts.create_from_content(agents, run_id=run_a["runId"], name="model.xlsx",
                                          content="A", bucket=BUCKET, s3=s3)
        b = artifacts.create_from_content(agents, run_id=run_b["runId"], name="model.xlsx",
                                          content="B", bucket=BUCKET, s3=s3)

        assert a["artifactId"] != b["artifactId"]
        assert a["storageKey"] != b["storageKey"]
        assert s3.get_object(Bucket=BUCKET, Key=a["storageKey"])["Body"].read() == b"A"
        assert s3.get_object(Bucket=BUCKET, Key=b["storageKey"])["Body"].read() == b"B"

    def test_repeated_name_within_one_run_does_not_collide_either(self, agents, a_run, s3):
        first = artifacts.create_from_content(agents, run_id=a_run["runId"], name="notes.md",
                                              content="draft one", bucket=BUCKET, s3=s3)
        second = artifacts.create_from_content(agents, run_id=a_run["runId"], name="notes.md",
                                               content="draft two", bucket=BUCKET, s3=s3)
        assert first["storageKey"] != second["storageKey"]
        assert s3.get_object(Bucket=BUCKET, Key=first["storageKey"])["Body"].read() == b"draft one"


class TestTenantIsolation:
    def test_another_tenant_cannot_read_it(self, two_stores, s3):
        owner_a, owner_b = two_stores
        owner_a.put({"pk": K.agent_pk(owner_a.owner_id, "fin"), "sk": "META",
                    "entity": "Agent", **AGENT})
        run = runs.create(owner_a, agent_id="fin", thread_id="dm-fin", goal="g")
        row = artifacts.create_from_content(owner_a, run_id=run["runId"], name="secret.md",
                                            content="confidential", bucket=BUCKET, s3=s3)

        with pytest.raises(NotFound):
            artifacts.get(owner_b, row["artifactId"])

    def test_same_tenant_another_agent_can_read_an_explicitly_referenced_artifact(
            self, agents, a_run, s3):
        row = artifacts.create_from_content(agents, run_id=a_run["runId"], name="brief.md",
                                            content="the brief", bucket=BUCKET, s3=s3)
        # The isolation boundary is ownerId (via Store), not which agent
        # created the row -- any Store for the same owner can resolve it,
        # which is exactly what lets a handoff's receiver read a reference.
        fetched = artifacts.get(agents, row["artifactId"])
        assert artifacts.read_content(fetched, bucket=BUCKET, s3=s3) == b"the brief"


class TestVersioning:
    def test_a_new_version_supersedes_but_keeps_the_original_retrievable(
            self, agents, a_run, s3):
        v1 = artifacts.create_from_content(agents, run_id=a_run["runId"], name="pricing.md",
                                           content="v1 content", bucket=BUCKET, s3=s3)

        v2 = artifacts.create_version_from_content(
            agents, v1["artifactId"], content="v2 content", run_id=a_run["runId"],
            bucket=BUCKET, s3=s3)

        assert v2["parentArtifactId"] == v1["artifactId"]
        assert v2["version"] == 2
        assert v2["name"] == v1["name"]

        parent_reloaded = artifacts.get(agents, v1["artifactId"])
        assert parent_reloaded["status"] == "superseded"
        # The original is still there, unmodified.
        assert artifacts.read_content(parent_reloaded, bucket=BUCKET, s3=s3) == b"v1 content"
        assert artifacts.read_content(v2, bucket=BUCKET, s3=s3) == b"v2 content"


class TestFailureIsolation:
    def test_a_failed_sibling_does_not_touch_a_completed_artifact(self, agents, s3):
        run_ok = runs.create(agents, agent_id="fin", thread_id="th-ok", goal="ok job")
        run_bad = runs.create(agents, agent_id="fin", thread_id="th-bad", goal="bad job")
        good = artifacts.create_from_content(agents, run_id=run_ok["runId"], name="good.md",
                                             content="fine", bucket=BUCKET, s3=s3)

        # The bad run never got far enough to create anything; failing it
        # must not touch the artifact the other run already produced.
        runs.advance(agents, run_bad, RunState.FAILED)

        reloaded = artifacts.get(agents, good["artifactId"])
        assert reloaded["status"] == "ready"

    def test_mark_failed_does_not_disturb_other_rows(self, agents, a_run, s3):
        ok = artifacts.create_from_content(agents, run_id=a_run["runId"], name="ok.md",
                                           content="fine", bucket=BUCKET, s3=s3)
        bad = artifacts.create_from_content(agents, run_id=a_run["runId"], name="bad.md",
                                            content="oops", bucket=BUCKET, s3=s3)

        artifacts.mark_failed(agents, bad["artifactId"], reason="upload verification failed")

        assert artifacts.get(agents, bad["artifactId"])["status"] == "failed"
        assert artifacts.get(agents, ok["artifactId"])["status"] == "ready"


class TestCreateArtifactTool:
    def _handle(self, store, run, agent, args, name="create_artifact"):
        push, ev, cost, turn = RecPush(), EvidenceWriter(run["runId"]), RunCost(), orch.Turn()
        return orch._handle_tool(store, run, agent, ev, push, resolution=None,
                                 parsed=_tool_call(name, args), seq=1,
                                 cost=cost, turn=turn), push

    def test_create_artifact_round_trips_through_the_real_dispatch(self, agents, a_run):
        # No EVIDENCE_BUCKET configured -- artifacts.create_from_content skips
        # the S3 put (same no-op-when-unconfigured idiom EvidenceWriter uses)
        # but still writes the DynamoDB row, so the tool contract is testable
        # without moto S3 wiring here.
        agent = agents.get(K.agent_pk(agents.owner_id, "fin"), "META")
        result, push = self._handle(agents, a_run, agent, {
            "name": "Launch Plan.md", "content": "the plan", "artifactType": "document"})

        assert result["toolResult"]["name"] == "Launch Plan.md"
        artifact_id = result["toolResult"]["artifactId"]
        row = artifacts.get(agents, artifact_id)
        assert row["status"] == "ready"
        assert row["createdByAgentId"] == "fin"
        tool_events = [e for e in push.sent if e.get("type") == "tool"]
        assert any(e["name"] == "create_artifact" for e in tool_events)

    def test_read_artifact_returns_content(self, agents, a_run, s3, monkeypatch):
        monkeypatch.setenv("EVIDENCE_BUCKET", BUCKET)
        monkeypatch.setattr(artifacts, "_s3", lambda given, bucket: s3)
        row = artifacts.create_from_content(agents, run_id=a_run["runId"], name="x.md",
                                            content="hello world", bucket=BUCKET, s3=s3)
        agent = agents.get(K.agent_pk(agents.owner_id, "fin"), "META")

        result, _push = self._handle(agents, a_run, agent, {"artifactId": row["artifactId"]},
                                     name="read_artifact")

        assert result["toolResult"]["content"] == "hello world"

    def test_reading_an_unknown_id_is_a_tool_error_not_a_crash(self, agents, a_run):
        agent = agents.get(K.agent_pk(agents.owner_id, "fin"), "META")
        result, _push = self._handle(agents, a_run, agent, {"artifactId": "art_nope"},
                                     name="read_artifact")
        assert "error" in result["toolResult"]


class TestHandoffArtifactRefs:
    COORDINATOR = {"agentId": "eng", "name": "Engineering", "status": "active",
                   "budget": {"maxConcurrentRuns": 5}}

    @pytest.fixture
    def two_agents(self, agents):
        agents.put({"pk": K.agent_pk(agents.owner_id, "eng"), "sk": "META",
                   "entity": "Agent", **self.COORDINATOR})
        return agents

    def test_a_handoffs_brief_names_referenced_artifacts_not_their_content(
            self, two_agents, a_run):
        produced = artifacts.create_from_content(
            two_agents, run_id=a_run["runId"], name="Q3 numbers.csv",
            content="revenue,cost\n100,40", artifact_type="dataset")

        coordinator_run = runs.create(two_agents, agent_id="eng", thread_id="dm-eng",
                                      goal="review Q3")
        handoff = orch._record_handoff(two_agents, coordinator_run, {
            "to": "fin", "goal": "double check these numbers",
            "artifactRefs": [produced["artifactId"]]})
        accepted = handoffs.accept(two_agents, coordinator_run, handoff,
                                   decided_by="system:auto-accept")

        assert "Q3 numbers.csv" in accepted["child"]["goal"]
        assert produced["artifactId"] in accepted["child"]["goal"]
        assert "revenue,cost" not in accepted["child"]["goal"]  # content never inlined

    def test_an_unresolvable_ref_is_dropped_not_fatal(self, two_agents, a_run):
        coordinator_run = runs.create(two_agents, agent_id="eng", thread_id="dm-eng",
                                      goal="review")
        handoff = orch._record_handoff(two_agents, coordinator_run, {
            "to": "fin", "goal": "look at this", "artifactRefs": ["art_madeup"]})
        accepted = handoffs.accept(two_agents, coordinator_run, handoff,
                                   decided_by="system:auto-accept")
        assert accepted["child"]["goal"] == "look at this"


class TestFanInDigestCarriesRefs:
    COORDINATOR = {"agentId": "eng", "name": "Engineering", "status": "active",
                   "budget": {"maxConcurrentRuns": 5}}

    def test_the_coordinators_wake_up_goal_names_artifacts_not_content(self, agents):
        agents.put({"pk": K.agent_pk(agents.owner_id, "eng"), "sk": "META",
                   "entity": "Agent", **self.COORDINATOR})
        coordinator_run = runs.create(agents, agent_id="eng", thread_id="dm-eng",
                                      goal="evaluate the launch")
        handoff = orch._record_handoff(agents, coordinator_run,
                                       {"to": "fin", "goal": "financial model"})
        accepted = handoffs.accept(agents, coordinator_run, handoff,
                                   decided_by="system:auto-accept")
        artifacts.create_from_content(agents, run_id=accepted["child"]["runId"],
                                      name="Pricing Model.xlsx",
                                      content="a whole financial model's worth of numbers",
                                      artifact_type="spreadsheet")

        continuation = handoffs.notify_coordinator_if_child(
            agents, accepted["child"], RunState.COMPLETED.value, "model built, three scenarios")

        assert "Pricing Model.xlsx" in continuation["goal"]
        assert "a whole financial model's worth of numbers" not in continuation["goal"]


class TestArtifactsApiRoutes:
    def test_list_and_fetch_and_delete(self, api_table, s3, monkeypatch):
        monkeypatch.setenv("EVIDENCE_BUCKET", BUCKET)
        import handlers.api as api
        store = Store("owner-a", table=api_table)
        store.put({"pk": K.agent_pk(store.owner_id, "fin"), "sk": "META",
                  "entity": "Agent", **AGENT})
        run = runs.create(store, agent_id="fin", thread_id="dm-fin", goal="g")
        row = artifacts.create_from_content(store, run_id=run["runId"], name="report.md",
                                            content="hi", bucket=BUCKET, s3=s3)

        status, body = call("GET", "/artifacts")
        assert status == 200
        assert any(f["artifactId"] == row["artifactId"] for f in body["artifacts"])
        card = next(f for f in body["artifacts"] if f["artifactId"] == row["artifactId"])
        assert card["name"] == "report.md"
        assert card["downloadUrl"]

        status, body = call("GET", f"/artifacts/{row['artifactId']}")
        assert status == 200
        assert body["name"] == "report.md"

        status, body = call("DELETE", f"/artifacts/{row['artifactId']}")
        assert status == 200
        assert body["status"] == "deleted"

        status, body = call("GET", "/artifacts")
        assert not any(f["artifactId"] == row["artifactId"] for f in body["artifacts"])

    def test_a_run_id_filter_narrows_to_that_runs_own_output(self, api_table, s3, monkeypatch):
        monkeypatch.setenv("EVIDENCE_BUCKET", BUCKET)
        store = Store("owner-a", table=api_table)
        store.put({"pk": K.agent_pk(store.owner_id, "fin"), "sk": "META",
                  "entity": "Agent", **AGENT})
        run_a = runs.create(store, agent_id="fin", thread_id="th-a", goal="a")
        run_b = runs.create(store, agent_id="fin", thread_id="th-b", goal="b")
        artifacts.create_from_content(store, run_id=run_a["runId"], name="a.md",
                                      content="a", bucket=BUCKET, s3=s3)
        artifacts.create_from_content(store, run_id=run_b["runId"], name="b.md",
                                      content="b", bucket=BUCKET, s3=s3)

        status, body = call("GET", "/artifacts", qs={"runId": run_a["runId"]})
        assert status == 200
        assert [f["name"] for f in body["artifacts"]] == ["a.md"]
