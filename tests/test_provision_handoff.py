"""The long half of first-Bot harness creation lives on the orchestrator.

The API function is killed at 30 seconds. This event has no runId; treating
it as a run would seal a phantom FAILED. A harness that is still CREATING
is reported pending and is not a run failure.
"""

import handlers.orchestrator as orch
from amazai import standard_runtime as RT


def test_provision_owner_event_does_not_need_a_run(monkeypatch, table):
    seen = {}

    def ensure(store, **kw):
        seen["owner"] = store.owner_id
        seen["wait"] = kw.get("ready_wait_seconds")
        seen["takeover"] = kw.get("takeover_after_wait")
        return "arn:ready"

    monkeypatch.setattr(orch.standard_runtime, "ensure_shared_harness", ensure)

    result = orch.handler({"provisionOwner": "owner-a"}, None)

    assert result == {"ok": True, "harnessArn": "arn:ready"}
    assert seen == {
        "owner": "owner-a",
        "wait": RT.WORKER_READY_SECONDS,
        "takeover": True,
    }


def test_still_creating_is_pending_and_does_not_invent_a_run(monkeypatch, table):
    def ensure(store, **kw):
        raise RT.StillCreating(
            "the account harness is still CREATING; retry this request")

    monkeypatch.setattr(orch.standard_runtime, "ensure_shared_harness", ensure)

    result = orch.handler({"provisionOwner": "owner-z"}, None)

    assert result["ok"] is False
    assert result["pending"] is True
    assert "still CREATING" in result["error"]
    assert "runId" not in result
