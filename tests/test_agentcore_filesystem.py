"""Filesystem mount merge for AgentCore's UpdateHarness.

UpdateHarness replaces `filesystemConfigurations` wholesale, so adding a mount
means reading the harness (GetHarness, which the stack grants) first and
merging. These cover the pure merge logic and the get-then-merge sequence with a
mocked control client, so no live AWS is needed. The write itself (UpdateHarness)
is deliberately ungranted in this deployment -- tools travel per-invoke, see
tests/test_iam_contract.py -- so `update_filesystem` fails closed after the
merge; enabling the write needs both an IAM grant and a real AgentCore
round-trip, flagged for real-AWS sign-off in the feature findings.
"""

import pytest

from amazai import agentcore
from amazai.agentcore import merge_filesystem_configurations

ARN = "arn:aws:bedrock-agentcore:us-west-2:1:harness/h-123"


def _session(path):
    return {"sessionStorage": {"mountPath": path}}


def _efs(path, fs_id="fs-1"):
    return {"efs": {"mountPath": path, "fileSystemId": fs_id}}


class TestMergeFilesystemConfigurations:
    def test_existing_mounts_are_carried_forward(self):
        # The whole point: UpdateHarness replaces the list, so an existing mount
        # not sent back would be dropped.
        merged = merge_filesystem_configurations([_session("/mnt/scratch")],
                                                 [_efs("/mnt/shared")])
        paths = {agentcore._mount_path(c) for c in merged}
        assert paths == {"/mnt/scratch", "/mnt/shared"}

    def test_a_remount_of_the_same_path_swaps_the_backing(self):
        # Two configs on one mountPath is rejected by the control plane; the
        # new mount wins so a re-mount changes the backing rather than duping.
        merged = merge_filesystem_configurations(
            [_session("/mnt/work")], [_efs("/mnt/work")])
        assert len(merged) == 1
        assert "efs" in merged[0]

    def test_order_is_existing_then_new(self):
        merged = merge_filesystem_configurations(
            [_session("/mnt/a")], [_efs("/mnt/b"), _efs("/mnt/c")])
        assert [agentcore._mount_path(c) for c in merged] == [
            "/mnt/a", "/mnt/b", "/mnt/c"]

    def test_empty_inputs_are_handled(self):
        assert merge_filesystem_configurations([], []) == []
        assert merge_filesystem_configurations(None, None) == []

    def test_only_new_mounts_when_harness_has_none(self):
        merged = merge_filesystem_configurations([], [_session("/mnt/scratch")])
        assert len(merged) == 1

    def test_a_mount_outside_mnt_is_rejected(self):
        with pytest.raises(ValueError, match="/mnt"):
            merge_filesystem_configurations([], [_session("/data/scratch")])

    def test_an_existing_mount_outside_mnt_is_also_rejected(self):
        # The invariant is enforced on rows already on the harness too, so a
        # malformed config can never be written back even if it somehow existed.
        with pytest.raises(ValueError, match="/mnt"):
            merge_filesystem_configurations([_session("/etc")], [_session("/mnt/ok")])

    def test_a_config_without_a_mount_path_is_rejected(self):
        with pytest.raises(ValueError, match="mountPath"):
            merge_filesystem_configurations([], [{"sessionStorage": {}}])

    def test_the_mnt_root_itself_is_allowed(self):
        merged = merge_filesystem_configurations([], [_session("/mnt")])
        assert agentcore._mount_path(merged[0]) == "/mnt"


class TestMergedFilesystemGetThenMerge:
    """`merged_filesystem` is the pure, permitted half of a filesystem change:
    the GetHarness read plus the merge. It never calls the ungranted
    UpdateHarness, so it can be exercised end to end with a mocked control
    client and no live AWS."""

    def test_reads_the_harness_then_returns_the_merged_list(self):
        class Control:
            def get_harness(self, *, harnessId):
                assert harnessId == "h-123"
                return {"filesystemConfigurations": [_session("/mnt/scratch")]}

        core = agentcore.AgentCore(runtime=object(), control=Control())
        merged = core.merged_filesystem(ARN, [_efs("/mnt/shared")])

        paths = {agentcore._mount_path(c) for c in merged}
        assert paths == {"/mnt/scratch", "/mnt/shared"}

    def test_a_harness_with_no_configs_key_starts_from_the_new_mounts(self):
        class Control:
            def get_harness(self, *, harnessId):
                return {}  # never had a filesystem block

        core = agentcore.AgentCore(runtime=object(), control=Control())
        merged = core.merged_filesystem(ARN, [_session("/mnt/scratch")])
        assert len(merged) == 1

    def test_a_bad_mount_is_rejected_during_the_merge(self):
        class Control:
            def get_harness(self, *, harnessId):
                return {"filesystemConfigurations": []}

        core = agentcore.AgentCore(runtime=object(), control=Control())
        with pytest.raises(ValueError):
            core.merged_filesystem(ARN, [_session("/tmp/nope")])


class TestUpdateFilesystemFailsClosed:
    """UpdateHarness is deliberately ungranted in this deployment (tools travel
    per-invoke; see tests/test_iam_contract.py). `update_filesystem` therefore
    does the permitted get-then-merge and then fails closed, rather than calling
    an API the runtime is not allowed to call."""

    def test_it_validates_the_merge_then_refuses_to_write(self):
        class Control:
            def get_harness(self, *, harnessId):
                return {"filesystemConfigurations": [_session("/mnt/scratch")]}

        core = agentcore.AgentCore(runtime=object(), control=Control())
        with pytest.raises(agentcore.HarnessNotReady, match="UpdateHarness"):
            core.update_filesystem(ARN, [_efs("/mnt/shared")])

    def test_a_bad_mount_is_rejected_before_the_fail_closed(self):
        class Control:
            def get_harness(self, *, harnessId):
                return {"filesystemConfigurations": []}

        core = agentcore.AgentCore(runtime=object(), control=Control())
        # The /mnt invariant is a ValueError from the merge, not the
        # HarnessNotReady fail-closed: a malformed mount never reaches the write.
        with pytest.raises(ValueError):
            core.update_filesystem(ARN, [_session("/tmp/nope")])
