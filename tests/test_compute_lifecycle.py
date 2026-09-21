"""The compute lifecycle wired into the real run record and Store.

These tests prove the claim FEAT-002 rests on: the compute abstraction
integrates into the EXISTING run lifecycle without a parallel run system and
without disturbing the RunState machine. Compute status is a field on the run
row, driven through the moto-backed Store, and for the default AgentCore
substrate every hook is a no-op that touches no AWS. A run that requires a full
computer resolves to EC2 Desktop, whose placeholder provider raises
NotImplementedError -- proving nothing is provisioned this phase.

Only the `store` fixture from conftest.py is used; no new table is invented.
"""

import pytest

from amazai import keys as K, runs
from amazai.compute import ComputeProvider, ComputeRequirements
from amazai.states import RunState


def _make_run(store, **kwargs):
    return runs.create(store, agent_id="agent-1", thread_id="dm-agent-1",
                       goal="do a thing", **kwargs)


class _FakeAgentCore:
    """An AgentCore stand-in that fails loudly if any AWS-ish call is made.

    The AgentCore compute provider's acquire/wait/release are pure no-ops, so a
    correct lifecycle never reaches these methods. If it ever did, the test
    would fail rather than silently touch a fake.
    """

    def invoke_stream(self, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("no AgentCore call must happen in the lifecycle hooks")

    def exec(self, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("no AgentCore call must happen in the lifecycle hooks")


def _agentcore_provider():
    from amazai.compute import AgentCoreProvider
    return AgentCoreProvider(core=_FakeAgentCore())


class TestDefaultRunPersistsAgentCore:
    def test_create_persists_all_false_requirements(self, store):
        run = _make_run(store)
        block = run["compute"]
        assert block["requirements"] == {
            "full_desktop": False, "persistent_dev_env": False,
            "os_level_app": False, "heavy_local_tooling": False,
        }
        assert block["assignment"] is None
        assert block["status"] == runs.COMPUTE_PENDING

    def test_default_run_resolves_agentcore(self, store):
        run = _make_run(store)
        run = runs.select_and_record_compute(store, run)
        assert run["compute"]["assignment"]["provider"] == ComputeProvider.AGENTCORE.value

    def test_block_survives_a_round_trip_through_the_store(self, store):
        run = _make_run(store)
        reread = store.get(run["pk"], "META")
        assert reread["compute"]["requirements"]["full_desktop"] is False
        assert reread["compute"]["status"] == runs.COMPUTE_PENDING


class TestAgentCoreLifecycleIsANoop:
    def test_acquire_wait_release_drives_status_without_aws(self, store):
        run = _make_run(store)
        provider = _agentcore_provider()

        run, prov, handle = runs.acquire_compute(store, run, {"agentId": "agent-1"},
                                                  provider=provider)
        assert prov is provider
        assert handle["ready"] is True
        assert run["compute"]["status"] == runs.COMPUTE_ACQUIRING

        run = runs.wait_until_ready(store, run, prov, handle)
        assert run["compute"]["status"] == runs.COMPUTE_READY

        run = runs.release_compute(store, run, prov, handle)
        assert run["compute"]["status"] == runs.COMPUTE_RELEASED

    def test_runstate_is_never_changed_by_the_hooks(self, store):
        run = _make_run(store)
        assert run["state"] == RunState.QUEUED.value
        provider = _agentcore_provider()
        run, prov, handle = runs.acquire_compute(store, run, provider=provider)
        run = runs.wait_until_ready(store, run, prov, handle)
        run = runs.release_compute(store, run, prov, handle)
        # The whole lifecycle ran and the run never left QUEUED: compute status
        # lives on a field, not a RunState.
        assert store.get(run["pk"], "META")["state"] == RunState.QUEUED.value

    def test_status_is_readable_back_from_the_store(self, store):
        run = _make_run(store)
        provider = _agentcore_provider()
        run, prov, handle = runs.acquire_compute(store, run, provider=provider)
        runs.wait_until_ready(store, run, prov, handle)
        assert store.get(run["pk"], "META")["compute"]["status"] == runs.COMPUTE_READY


class TestAssignmentPersistsOnTheRunRow:
    def test_assignment_is_written_and_reads_back(self, store):
        run = _make_run(store)
        run = runs.select_and_record_compute(store, run, {"agentId": "agent-1"})
        reread = store.get(run["pk"], "META")
        assignment = reread["compute"]["assignment"]
        assert assignment["provider"] == ComputeProvider.AGENTCORE.value
        assert "AgentCore" in assignment["reason"]
        assert assignment["requirements"]["full_desktop"] is False

    def test_selection_is_idempotent(self, store):
        run = _make_run(store)
        run = runs.select_and_record_compute(store, run)
        first = run["compute"]["assignment"]
        run = runs.select_and_record_compute(store, run)
        assert run["compute"]["assignment"] == first


class TestFullDesktopRunSelectsEc2AndDoesNotProvision:
    def test_seeded_requirements_persist_and_resolve_ec2(self, store):
        run = _make_run(store, compute_requirements=ComputeRequirements(full_desktop=True))
        assert run["compute"]["requirements"]["full_desktop"] is True
        run = runs.select_and_record_compute(store, run)
        assert run["compute"]["assignment"]["provider"] == ComputeProvider.EC2_DESKTOP.value

    def test_dict_requirements_are_accepted(self, store):
        run = _make_run(store, compute_requirements={"persistent_dev_env": True})
        run = runs.select_and_record_compute(store, run)
        assert run["compute"]["assignment"]["provider"] == ComputeProvider.EC2_DESKTOP.value

    def test_agent_requirement_routes_the_run_to_ec2(self, store):
        run = _make_run(store)
        agent = {"agentId": "agent-1", "compute": {"requirements": {"os_level_app": True}}}
        run = runs.select_and_record_compute(store, run, agent)
        assert run["compute"]["assignment"]["provider"] == ComputeProvider.EC2_DESKTOP.value

    def test_acquiring_ec2_hits_the_placeholder_and_raises(self, store):
        run = _make_run(store, compute_requirements=ComputeRequirements(full_desktop=True))
        # No provider injected: the hook builds the real EC2 placeholder from the
        # recorded assignment. Acquiring it must raise, proving no EC2/SSM call
        # succeeds and nothing is provisioned this phase.
        with pytest.raises(NotImplementedError):
            runs.acquire_compute(store, run, {"agentId": "agent-1"})

    def test_ec2_assignment_is_recorded_before_the_placeholder_raises(self, store):
        run = _make_run(store, compute_requirements=ComputeRequirements(heavy_local_tooling=True))
        with pytest.raises(NotImplementedError):
            runs.acquire_compute(store, run, {"agentId": "agent-1"})
        # The assignment was persisted (a legitimate store.update) before the
        # placeholder acquire raised; only provisioning is missing.
        reread = store.get(K.run_pk(run["runId"]), "META")
        assert reread["compute"]["assignment"]["provider"] == ComputeProvider.EC2_DESKTOP.value
