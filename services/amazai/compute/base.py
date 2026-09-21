"""The provider-agnostic compute interface.

Implements the lifecycle contract from `docs/architecture/05-run-lifecycle.md`:
compute is acquired around a run, waited on until ready, used to execute the
run's work, then released. Both the AgentCore adapter and the EC2 Desktop
placeholder implement this single shape so the orchestrator drives either
provider through the same four hooks.

Defined as a ``typing.Protocol`` (structural) so adapters need not inherit from
it; they only need to satisfy the shape. The ``provider`` attribute reports
which ``ComputeProvider`` an implementation is.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .types import ComputeProvider


@runtime_checkable
class ComputeProviderInterface(Protocol):
    """Lifecycle contract every compute provider must satisfy.

    The lifecycle is ``acquire -> wait_until_ready -> execute -> release``. Each
    hook is provider-agnostic; for AgentCore several hooks are effectively
    no-ops (sessions are ephemeral and managed by the runtime), while for EC2
    Desktop they map onto instance lookup/start, SSM readiness, command
    execution, and workspace persistence + stop.
    """

    @property
    def provider(self) -> ComputeProvider:
        """The ``ComputeProvider`` enum value this implementation serves."""
        ...

    def acquire(self, run: dict, agent: dict, *, store: Any = None) -> Any:
        """Acquire compute for a run and return an opaque handle/context.

        The handle is passed back to the remaining lifecycle hooks. Providers
        must reuse the existing approvals, evidence, secrets, and audit systems
        for any consequential action taken while acquiring.
        """
        ...

    def wait_until_ready(self, handle: Any, *, store: Any = None) -> Any:
        """Block/poll until the acquired compute is ready to accept work."""
        ...

    def execute(self, handle: Any, *, store: Any = None, **kwargs: Any) -> Any:
        """Run the requested work on the acquired compute."""
        ...

    def release(self, handle: Any, *, store: Any = None) -> Any:
        """Release the compute, persisting any durable workspace first."""
        ...
