"""Pure compute-routing policy.

Implements the compute-selection rule from `docs/architecture/05-run-lifecycle.md`:
a run stays on the default ephemeral AgentCore runtime unless it explicitly
requires a full computer, in which case it routes to EC2 Desktop.

Mirrors the STYLE of `router.select_path`: pure inputs
(``ComputeRequirements``, and optionally the run/agent dicts) map to a frozen
``ComputeAssignment``. No AWS calls, no store access, no side effects, so the
decision is trivially testable and reproducible. The default is the safe path
(AgentCore) so absent or backward-compatible records never accidentally route to
EC2.
"""

from __future__ import annotations

from .types import ComputeAssignment, ComputeProvider, ComputeRequirements

#: Human-readable reason attached to an AgentCore assignment.
_REASON_AGENTCORE = "no full-computer requirement; default AgentCore"

#: Ordered flag -> reason mapping, so the assignment names the first specific
#: full-computer requirement that triggered EC2 selection.
_EC2_REASONS: tuple[tuple[str, str], ...] = (
    ("full_desktop", "requires a full graphical desktop"),
    ("persistent_dev_env", "requires a persistent developer environment"),
    ("os_level_app", "requires an OS-level application"),
    ("heavy_local_tooling", "requires heavy local tooling"),
)


def select_compute_provider(requirements: ComputeRequirements) -> ComputeAssignment:
    """Choose a compute provider from pure requirements.

    Returns ``EC2_DESKTOP`` only when ``requirements.requires_full_computer`` is
    True (any full-computer flag set); otherwise ``AGENTCORE``. The assignment's
    ``reason`` names why.
    """
    if requirements.requires_full_computer:
        reason = next(
            (why for flag, why in _EC2_REASONS if getattr(requirements, flag)),
            "requires a full computer",
        )
        return ComputeAssignment(
            provider=ComputeProvider.EC2_DESKTOP,
            reason=reason,
            requirements=requirements,
        )
    return ComputeAssignment(
        provider=ComputeProvider.AGENTCORE,
        reason=_REASON_AGENTCORE,
        requirements=requirements,
    )


def select_for_run(run: dict | None = None, agent: dict | None = None) -> ComputeAssignment:
    """Convenience: build requirements from run/agent records, then select.

    Requirements from the run and the agent are OR-ed flag by flag, so a
    requirement declared on either record is honoured. Absent compute fields
    default to all-False (=> AgentCore), preserving backward compatibility.
    """
    run_requirements = ComputeRequirements.from_run(run)
    agent_requirements = ComputeRequirements.from_agent(agent)
    merged = ComputeRequirements(
        full_desktop=run_requirements.full_desktop or agent_requirements.full_desktop,
        persistent_dev_env=(
            run_requirements.persistent_dev_env or agent_requirements.persistent_dev_env
        ),
        os_level_app=run_requirements.os_level_app or agent_requirements.os_level_app,
        heavy_local_tooling=(
            run_requirements.heavy_local_tooling or agent_requirements.heavy_local_tooling
        ),
    )
    return select_compute_provider(merged)
