"""Deployment-time provisioning agrees with lazy account-runtime provisioning."""

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


def test_reprovision_preserves_old_harness_for_v1_resume(table):
    store = Store("owner-a", table=table)
    old = "arn:aws:bedrock-agentcore:us-west-2:1:harness/old-dedicated"
    store.put({
        "pk": K.agent_pk("eng"), "sk": "META", "entity": "Agent", "agentId": "eng",
        "harnessArn": old, "executionRoleArn": "arn:aws:iam::1:role/old",
        "systemPrompt": "edited by operator", "state": "active",
    })

    P.write_agent(store, SEAT, ARN, ROLE, runtime_mode="shared")

    row = store.get(K.agent_pk("eng"), "META")
    assert row["harnessArn"] == old
    assert row["executionRoleArn"] == "arn:aws:iam::1:role/old"
    assert "runtimeMode" not in row
    assert row["systemPrompt"] == "edited by operator"
