"""Deployment-time provisioning agrees with lazy account-runtime provisioning."""

import threading
import time

import pytest

from amazai import keys as K
from amazai.store import Store
from scripts import provision_agents as P

ROLE = "arn:aws:iam::1:role/amazai-agent-dynamic"
ARN = "arn:aws:bedrock-agentcore:us-west-2:1:harness/shared-1234567890"


class Pages:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self):
        return self.pages


class Control:
    def __init__(self, *, existing=False, status="READY", role=ROLE, nested=False):
        self.existing = existing
        self.status = status
        self.role = role
        self.nested = nested
        self.created = []
        self.got = []

    def get_paginator(self, name):
        assert name == "list_harnesses"
        rows = [{"harnessName": "amazai_shared_test", "harnessArn": ARN}] if self.existing else []
        return Pages([{"harnessSummaries": rows}])

    def list_harnesses(self, **request):
        rows = [{"harnessName": "amazai_shared_test", "harnessArn": ARN}] if self.existing else []
        return {"harnessSummaries": rows}

    def create_harness(self, **kwargs):
        self.created.append(kwargs)
        return {"harness": {"arn": ARN}} if self.nested else {"harnessArn": ARN}

    def get_harness(self, **kwargs):
        self.got.append(kwargs)
        return {"harness": {"status": self.status, "executionRoleArn": self.role}}


SEAT = {
    "key": "eng", "name": "Engineering", "role": "Builds the product.",
    "accent": "#4F86F7", "modelId": "us.example.model", "maxTokens": 1000,
    "tools": [], "workspaceMode": "ephemeral",
    "budget": {"perRunUsd": 1.0, "perMonthUsd": 10.0},
}

def test_existing_harness_is_reused_and_verified():
    control = Control(existing=True)
    assert P.ensure_harness(control, "amazai_shared_test", ROLE, "us-west-2") == ARN
    assert control.created == []
    assert control.got == [{"harnessId": "shared-1234567890"}]

def test_nested_current_create_response_is_accepted():
    control = Control(nested=True)
    assert P.ensure_harness(control, "amazai_shared_test", ROLE, "us-west-2") == ARN
    assert control.created == [{"harnessName": "amazai_shared_test", "executionRoleArn": ROLE}]

def test_wrong_role_is_refused():
    control = Control(existing=True, role="arn:aws:iam::1:role/admin")
    with pytest.raises(RuntimeError, match="not the restricted role"):
        P.ensure_harness(control, "amazai_shared_test", ROLE, "us-west-2")

def test_failed_harness_is_refused():
    control = Control(existing=True, status="CREATE_FAILED")
    with pytest.raises(RuntimeError, match="CREATE_FAILED"):
        P.ensure_harness(control, "amazai_shared_test", ROLE, "us-west-2")

def test_new_seat_is_written_as_a_logical_bot_on_shared_compute(table):
    store = Store("owner-a", table=table)
    P.write_agent(store, SEAT, ARN, ROLE, runtime_mode="shared")
    row = store.get(K.agent_pk("eng"), "META")
    assert row["harnessArn"] == ARN and row["runtimeMode"] == "shared"
    thread = store.get(K.thread_pk("dm-eng"), "META")
    assert thread["sessionId"] == K.bot_session_id("owner-a", "eng", "dm-eng")

def test_reprovision_records_shared_and_dedicated_targets_for_v1_resume(table):
    store = Store("owner-a", table=table)
    old = "arn:aws:bedrock-agentcore:us-west-2:1:harness/old-dedicated"
    store.put({
        "pk": K.agent_pk("eng"), "sk": "META", "entity": "Agent", "agentId": "eng",
        "harnessArn": old, "executionRoleArn": "arn:aws:iam::1:role/old",
        "systemPrompt": "edited by operator", "state": "active",
    })

    P.write_agent(store, SEAT, ARN, ROLE, runtime_mode="shared")

    row = store.get(K.agent_pk("eng"), "META")
    assert row["harnessArn"] == ARN
    assert row["sharedHarnessArn"] == ARN
    assert row["dedicatedHarnessArn"] == old
    assert row["runtimeMode"] == "shared"
    assert row["executionRoleArn"] == ROLE
    assert row["systemPrompt"] == "edited by operator"

def test_dedicated_reprovision_does_not_restore_the_shared_arn(table):
    store = Store("owner-a", table=table)
    P.write_agent(store, SEAT, ARN, ROLE, runtime_mode="shared")
    dedicated = "arn:aws:bedrock-agentcore:us-west-2:1:harness/eng-dedicated"

    P.write_agent(store, SEAT, dedicated, "arn:aws:iam::1:role/eng", runtime_mode="dedicated")

    row = store.get(K.agent_pk("eng"), "META")
    assert row["runtimeMode"] == "dedicated"
    assert row["harnessArn"] == row["dedicatedHarnessArn"] == dedicated
    assert row["sharedHarnessArn"] == ARN


def test_deploy_provisioner_adopts_the_lazy_provisioners_rotated_generation(table):
    store = Store("owner-a", table=table)
    first = P.standard_runtime.shared_harness_name(store.owner_id)
    store.put({
        "pk": K.user_pk(store.owner_id), "sk": K.runtime_sk(),
        "entity": "AccountRuntime", "runtimeKind": "standard",
        "state": "failed", "harnessName": first, "harnessArn": None,
        "generation": 1, "rotateName": True,
        "executionRoleArn": ROLE, "claimToken": "failed-one",
        "claimedAt": "2020-01-01T00:00:00Z",
    })
    control = Control()

    arn, runtime = P.ensure_account_harness(store, control, ROLE)

    assert arn == ARN
    assert runtime["generation"] == 2
    assert runtime["harnessName"] == P.standard_runtime.shared_harness_name("owner-a", 2)
    assert control.created[0]["harnessName"] == runtime["harnessName"]


def test_lazy_and_deploy_first_claim_create_one_harness_and_both_adopt_it(table, monkeypatch):
    """The two production entry points racing, not two calls to one helper."""
    lazy_store = Store("owner-a", table=table)
    deploy_store = Store("owner-a", table=table)
    create_started = threading.Event()
    release_create = threading.Event()

    class RacingControl:
        def __init__(self):
            self.created = []

        def list_harnesses(self, **request):
            return {"harnessSummaries": []}

        def create_harness(self, **kwargs):
            self.created.append(kwargs)
            create_started.set()
            assert release_create.wait(2), "test never let the winning create finish"
            return {"harness": {"arn": ARN}}

        def get_harness(self, **kwargs):
            return {"harness": {"status": "READY", "executionRoleArn": ROLE}}

    control = RacingControl()
    core = P.agentcore.AgentCore(runtime=object(), control=control)
    monkeypatch.setattr(P.standard_runtime, "POLL_SECONDS", 0.01)
    results = []
    failures = []

    def lazy():
        try:
            results.append(("lazy", P.standard_runtime.ensure_shared_harness(
                lazy_store, client=core, role_arn=ROLE,
                wait_seconds=2, ready_wait_seconds=0)))
        except Exception as exc:  # pragma: no cover - reported below
            failures.append(exc)

    def deploy():
        try:
            arn, _row = P.ensure_account_harness(deploy_store, control, ROLE)
            results.append(("deploy", arn))
        except Exception as exc:  # pragma: no cover - reported below
            failures.append(exc)

    first = threading.Thread(target=lazy)
    second = threading.Thread(target=deploy)
    first.start()
    assert create_started.wait(1), "lazy provisioner never reached CreateHarness"
    second.start()
    # Give the deploy path time to observe the live claim and enter its strong
    # read loop before the winner publishes READY.
    time.sleep(0.05)
    release_create.set()
    first.join(3)
    second.join(3)

    assert failures == []
    assert sorted(results) == [("deploy", ARN), ("lazy", ARN)]
    assert len(control.created) == 1
    row = lazy_store.get(K.user_pk("owner-a"), K.runtime_sk(), consistent=True)
    assert row["state"] == "ready" and row["harnessArn"] == ARN
