"""EC2 Desktop compute provider: interface-only placeholder (no provisioning).

Defines the lifecycle a later phase (DEV-01) will implement for the narrow
"full computer" escape hatch, WITHOUT making any live boto3 EC2/SSM calls this
phase. Every method either raises ``NotImplementedError`` (mirroring
``agentcore.AgentCore.update_filesystem``) or returns a documented placeholder,
so importing and wiring this provider never provisions anything.

Design intent the next phase MUST preserve:

- EC2 actions flow through the SAME approvals, evidence, secrets, and audit
  systems as AgentCore. They are reused, never forked: starting/stopping an
  instance or running a command is a consequential action gated by the existing
  approvals path and recorded via the existing evidence/audit path, and any
  credentials come from the existing secrets path.
- Workspace persistence aligns with the S3-versioned-drive-as-system-of-record
  model in `docs/architecture/04-workspaces.md`: the durable drive is restored
  onto the instance on acquire and persisted back to versioned S3 on release, so
  the instance itself holds no system-of-record state.

The lifecycle maps onto `base.py` as:
``acquire`` = lookup + start, ``wait_until_ready`` = SSM readiness,
``execute`` = run_command, ``release`` = persist_workspace + stop_instance.
"""

from __future__ import annotations

from typing import Any

from .types import ComputeProvider

#: Raised (indirectly, via the granular methods) to make clear that EC2 Desktop
#: is not provisioned in this phase.
_NOT_PROVISIONED = (
    "EC2 Desktop is a placeholder in this phase; no EC2/SSM provisioning is "
    "performed. This will be implemented in a later phase behind the same "
    "approvals, evidence, secrets, and audit systems as AgentCore."
)


class EC2DesktopProvider:
    """Placeholder EC2 Desktop provider defining the future lifecycle contract."""

    @property
    def provider(self) -> ComputeProvider:
        return ComputeProvider.EC2_DESKTOP

    # --- granular EC2/SSM steps (not implemented this phase) ----------------

    def lookup_dedicated_instance(self, run: dict, agent: dict, *, store: Any = None) -> Any:
        """Find the dedicated EC2 instance assigned to this owner/agent.

        Future: resolves the persistent machine record. No live EC2 call is made
        this phase.
        """
        raise NotImplementedError(_NOT_PROVISIONED)

    def start_instance(self, instance_id: str, *, store: Any = None) -> Any:
        """Start the dedicated instance.

        Future: a consequential action gated by the existing approvals path and
        recorded via the existing evidence/audit path.
        """
        raise NotImplementedError(_NOT_PROVISIONED)

    def wait_for_ssm_ready(self, instance_id: str, *, store: Any = None) -> Any:
        """Wait until Systems Manager reports the instance ready for commands.

        Future: polls SSM instance information; no live call this phase.
        """
        raise NotImplementedError(_NOT_PROVISIONED)

    def restore_workspace(self, instance_id: str, *, store: Any = None) -> Any:
        """Restore the durable workspace from versioned S3 onto the instance.

        Future: aligns with the S3-versioned-drive-as-system-of-record model in
        docs/architecture/04-workspaces.md.
        """
        raise NotImplementedError(_NOT_PROVISIONED)

    def run_command(self, instance_id: str, command: str, *, store: Any = None) -> Any:
        """Execute a command on the instance (via SSM).

        Future: a consequential action gated by approvals and recorded via
        evidence/audit, using credentials from the existing secrets path.
        """
        raise NotImplementedError(_NOT_PROVISIONED)

    def persist_workspace(self, instance_id: str, *, store: Any = None) -> Any:
        """Persist the workspace back to versioned S3.

        Future: writes the durable drive back to versioned S3 so the instance
        holds no system-of-record state (docs/architecture/04-workspaces.md).
        """
        raise NotImplementedError(_NOT_PROVISIONED)

    def stop_instance(self, instance_id: str, *, store: Any = None) -> Any:
        """Stop the dedicated instance.

        Future: a consequential action gated by approvals and recorded via
        evidence/audit.
        """
        raise NotImplementedError(_NOT_PROVISIONED)

    # --- base.py lifecycle mapping (not implemented this phase) -------------

    def acquire(self, run: dict, agent: dict, *, store: Any = None) -> Any:
        """acquire = lookup_dedicated_instance + start_instance (future)."""
        raise NotImplementedError(_NOT_PROVISIONED)

    def wait_until_ready(self, handle: Any, *, store: Any = None) -> Any:
        """wait_until_ready = wait_for_ssm_ready (future)."""
        raise NotImplementedError(_NOT_PROVISIONED)

    def execute(self, handle: Any, *, store: Any = None, **kwargs: Any) -> Any:
        """execute = run_command (future)."""
        raise NotImplementedError(_NOT_PROVISIONED)

    def release(self, handle: Any, *, store: Any = None) -> Any:
        """release = persist_workspace + stop_instance (future)."""
        raise NotImplementedError(_NOT_PROVISIONED)
