"""Hybrid compute-provider abstraction.

WHY this package exists: AgentCore stays the default ephemeral runtime for every
run. A small number of runs genuinely need a *full computer* (a persistent
graphical desktop, a long-lived developer environment, an OS-level application,
or heavy local tooling) that the ephemeral AgentCore microVM cannot provide.
Rather than scattering EC2 logic across runs/agents/router, all of it lives
behind ONE provider interface here, and a pure policy decides which provider a
run gets. This keeps AgentCore unchanged and makes the EC2 path a narrow,
optional escape hatch that a later phase can fill in.

See `docs/architecture/04-workspaces.md` (durable S3-versioned drive as the
system of record, which EC2 workspace persistence must align with) and
`docs/architecture/05-run-lifecycle.md` (where compute is acquired and released
around a run).

Public surface (import from ``amazai.compute``):

- ``ComputeProvider`` -- enum of the two substrates.
- ``ComputeRequirements`` -- frozen full-computer requirement flags.
- ``ComputeAssignment`` -- frozen routing decision (provider + reason).
- ``ComputeProviderInterface`` -- the provider lifecycle Protocol.
- ``AgentCoreProvider`` -- adapter over the existing ``amazai.agentcore.AgentCore``.
- ``EC2DesktopProvider`` -- placeholder for the EC2 Desktop escape hatch.
- ``select_compute_provider`` / ``select_for_run`` -- the pure routing policy.
- ``get_provider`` -- factory from a ``ComputeProvider`` to a live provider.
"""

from __future__ import annotations

from typing import Any

from .agentcore_provider import AgentCoreProvider
from .base import ComputeProviderInterface
from .ec2_provider import EC2DesktopProvider
from .policy import select_compute_provider, select_for_run
from .types import ComputeAssignment, ComputeProvider, ComputeRequirements

__all__ = [
    "ComputeProvider",
    "ComputeRequirements",
    "ComputeAssignment",
    "ComputeProviderInterface",
    "AgentCoreProvider",
    "EC2DesktopProvider",
    "select_compute_provider",
    "select_for_run",
    "get_provider",
]


def get_provider(provider: ComputeProvider, **deps: Any) -> ComputeProviderInterface:
    """Return a live provider object for a ``ComputeProvider`` enum value.

    Lets the orchestrator go from a ``ComputeAssignment`` to a provider in one
    call. Extra keyword arguments are passed to the concrete provider's
    constructor (e.g. an injected ``AgentCore`` for testability).
    """
    if provider is ComputeProvider.AGENTCORE:
        return AgentCoreProvider(**deps)
    if provider is ComputeProvider.EC2_DESKTOP:
        return EC2DesktopProvider(**deps)
    raise ValueError(f"unknown compute provider: {provider!r}")
