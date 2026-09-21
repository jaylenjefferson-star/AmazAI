import pytest

from amazai.compute import (
    AgentCoreProvider,
    ComputeAssignment,
    ComputeProvider,
    ComputeRequirements,
    EC2DesktopProvider,
    get_provider,
    select_compute_provider,
    select_for_run,
)

FLAGS = ("full_desktop", "persistent_dev_env", "os_level_app", "heavy_local_tooling")


class TestDefaultSelection:
    def test_empty_run_selects_agentcore(self):
        assignment = select_for_run(run={})
        assert assignment.provider is ComputeProvider.AGENTCORE

    def test_run_with_no_compute_field_selects_agentcore(self):
        assignment = select_for_run(run={"id": "run_1", "state": "QUEUED"})
        assert assignment.provider is ComputeProvider.AGENTCORE

    def test_agent_with_no_compute_field_selects_agentcore(self):
        assignment = select_for_run(agent={"id": "agent_1", "role": "engineer"})
        assert assignment.provider is ComputeProvider.AGENTCORE

    def test_none_inputs_select_agentcore(self):
        assignment = select_for_run()
        assert assignment.provider is ComputeProvider.AGENTCORE

    def test_all_false_requirements_select_agentcore(self):
        assignment = select_compute_provider(ComputeRequirements())
        assert assignment.provider is ComputeProvider.AGENTCORE


class TestEc2Selection:
    @pytest.mark.parametrize("flag", FLAGS)
    def test_each_flag_individually_selects_ec2(self, flag):
        requirements = ComputeRequirements(**{flag: True})
        assignment = select_compute_provider(requirements)
        assert assignment.provider is ComputeProvider.EC2_DESKTOP

    @pytest.mark.parametrize("flag", FLAGS)
    def test_flag_on_run_record_selects_ec2(self, flag):
        run = {"compute": {"requirements": {flag: True}}}
        assignment = select_for_run(run=run)
        assert assignment.provider is ComputeProvider.EC2_DESKTOP

    @pytest.mark.parametrize("flag", FLAGS)
    def test_flag_on_agent_record_selects_ec2(self, flag):
        agent = {"compute": {"requirements": {flag: True}}}
        assignment = select_for_run(agent=agent)
        assert assignment.provider is ComputeProvider.EC2_DESKTOP


class TestRequiresFullComputer:
    def test_default_is_false(self):
        assert ComputeRequirements().requires_full_computer is False

    @pytest.mark.parametrize("flag", FLAGS)
    def test_true_when_any_flag_set(self, flag):
        assert ComputeRequirements(**{flag: True}).requires_full_computer is True


class TestAssignmentReason:
    def test_agentcore_reason_names_default(self):
        assignment = select_compute_provider(ComputeRequirements())
        assert "AgentCore" in assignment.reason

    def test_ec2_reason_names_the_requirement(self):
        assignment = select_compute_provider(
            ComputeRequirements(persistent_dev_env=True)
        )
        assert "persistent developer environment" in assignment.reason

    def test_assignment_as_record_is_json_safe(self):
        assignment = select_compute_provider(ComputeRequirements(full_desktop=True))
        record = assignment.as_record()
        assert record["provider"] == "ec2_desktop"
        assert isinstance(record["reason"], str)
        assert record["requirements"]["full_desktop"] is True

    def test_to_dict_matches_as_record(self):
        assignment = select_compute_provider(ComputeRequirements())
        assert assignment.to_dict() == assignment.as_record()


class TestComputeProviderEnum:
    def test_is_str_enum(self):
        assert ComputeProvider.AGENTCORE == "agentcore"
        assert ComputeProvider.EC2_DESKTOP == "ec2_desktop"


class TestGetProvider:
    def test_returns_agentcore_provider(self):
        provider = get_provider(ComputeProvider.AGENTCORE, core=_FakeAgentCore())
        assert isinstance(provider, AgentCoreProvider)
        assert provider.provider is ComputeProvider.AGENTCORE

    def test_returns_ec2_provider(self):
        provider = get_provider(ComputeProvider.EC2_DESKTOP)
        assert isinstance(provider, EC2DesktopProvider)
        assert provider.provider is ComputeProvider.EC2_DESKTOP


class _FakeAgentCore:
    """Records delegated calls without touching AWS."""

    def __init__(self):
        self.invoke_stream_calls = []
        self.exec_calls = []

    def invoke_stream(self, **kwargs):
        self.invoke_stream_calls.append(kwargs)
        return ["event"]

    def exec(self, **kwargs):
        self.exec_calls.append(kwargs)
        return {"ok": True}


class TestAgentCoreProviderDelegation:
    def test_constructed_with_injected_agentcore(self):
        fake = _FakeAgentCore()
        provider = AgentCoreProvider(core=fake)
        assert provider.core is fake
        assert provider.provider is ComputeProvider.AGENTCORE

    def test_acquire_is_ready_noop(self):
        provider = AgentCoreProvider(core=_FakeAgentCore())
        handle = provider.acquire({"id": "run_1"}, {"id": "agent_1"})
        assert handle["ready"] is True
        assert provider.wait_until_ready(handle) is True
        assert provider.release(handle) is None

    def test_execute_delegates_to_invoke_stream(self):
        fake = _FakeAgentCore()
        provider = AgentCoreProvider(core=fake)
        result = provider.execute(
            None,
            mode="invoke_stream",
            harness_arn="arn",
            session_id="s",
            messages=[],
            model_id="m",
            system_prompt="p",
        )
        assert result == ["event"]
        assert len(fake.invoke_stream_calls) == 1
        assert fake.exec_calls == []

    def test_execute_delegates_to_exec(self):
        fake = _FakeAgentCore()
        provider = AgentCoreProvider(core=fake)
        result = provider.execute(
            None, mode="exec", harness_arn="arn", session_id="s", command="ls"
        )
        assert result == {"ok": True}
        assert len(fake.exec_calls) == 1
        assert fake.invoke_stream_calls == []

    def test_unknown_execute_mode_raises(self):
        provider = AgentCoreProvider(core=_FakeAgentCore())
        with pytest.raises(ValueError):
            provider.execute(None, mode="teleport")


class TestEc2DesktopPlaceholder:
    @pytest.mark.parametrize(
        "method, args",
        [
            ("lookup_dedicated_instance", ({}, {})),
            ("start_instance", ("i-123",)),
            ("wait_for_ssm_ready", ("i-123",)),
            ("restore_workspace", ("i-123",)),
            ("run_command", ("i-123", "ls")),
            ("persist_workspace", ("i-123",)),
            ("stop_instance", ("i-123",)),
        ],
    )
    def test_granular_methods_not_implemented(self, method, args):
        provider = EC2DesktopProvider()
        with pytest.raises(NotImplementedError):
            getattr(provider, method)(*args)

    def test_lifecycle_methods_not_implemented(self):
        provider = EC2DesktopProvider()
        with pytest.raises(NotImplementedError):
            provider.acquire({}, {})
        with pytest.raises(NotImplementedError):
            provider.wait_until_ready(None)
        with pytest.raises(NotImplementedError):
            provider.execute(None)
        with pytest.raises(NotImplementedError):
            provider.release(None)
