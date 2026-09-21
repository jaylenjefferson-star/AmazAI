"""Value objects for the compute-provider abstraction.

Implements the compute layer described in `docs/architecture/04-workspaces.md`
(durable S3-versioned drive as system of record) and
`docs/architecture/05-run-lifecycle.md` (where compute is acquired and released
around a run). These are pure value objects: an enum naming the two providers,
a frozen requirements record describing when a run needs a *full computer*, and
a frozen assignment record capturing the routing decision so it can be persisted
on the run.

Kept in `types.py` on purpose: naming it `models.py` would shadow the top-level
`amazai.models` module, and the shadowing/naming guards
(`tests/test_no_shadowed_modules.py`) treat that as a defect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ComputeProvider(str, Enum):
    """Which compute substrate a run executes on.

    Follows the `(str, Enum)` idiom used by `states.RunState` and
    `router.ToolPath`, so the value serialises directly onto a run record.
    AgentCore is the default ephemeral runtime; EC2 Desktop is a narrow,
    optional escape hatch for runs that genuinely need a full computer.
    """

    AGENTCORE = "agentcore"
    EC2_DESKTOP = "ec2_desktop"


#: Names of the boolean flags that describe a full-computer requirement. Kept in
#: one place so `from_run`/`from_agent` and `requires_full_computer` stay in sync
#: with the dataclass fields.
_REQUIREMENT_FLAGS = (
    "full_desktop",
    "persistent_dev_env",
    "os_level_app",
    "heavy_local_tooling",
)


@dataclass(frozen=True)
class ComputeRequirements:
    """Explicit, additive flags describing when a run needs a full computer.

    Every flag defaults to ``False`` so a record with no ``compute`` field
    resolves to all-False and therefore routes to AgentCore. This keeps the
    abstraction backward compatible: existing agents and runs predate the
    compute field and must continue to run on AgentCore untouched.

    - ``full_desktop``: needs a persistent graphical desktop / OS session.
    - ``persistent_dev_env``: needs a developer environment that survives across
      turns/runs (long-lived toolchains, caches, checkouts).
    - ``os_level_app``: needs to drive an OS-level application that cannot run
      inside the ephemeral AgentCore microVM.
    - ``heavy_local_tooling``: needs heavy local tooling (large builds, GPUs,
      simulators) unsuited to the ephemeral runtime.
    """

    full_desktop: bool = False
    persistent_dev_env: bool = False
    os_level_app: bool = False
    heavy_local_tooling: bool = False

    @property
    def requires_full_computer(self) -> bool:
        """True iff any full-computer flag is set."""
        return any(getattr(self, name) for name in _REQUIREMENT_FLAGS)

    @classmethod
    def _from_nested(cls, record: dict | None) -> ComputeRequirements:
        """Build from an optional ``{'compute': {'requirements': {...}}}`` record.

        Missing containers and missing flags default to ``False`` so absent
        compute config always means "no full-computer requirement".
        """
        record = record or {}
        compute = record.get("compute") or {}
        requirements = compute.get("requirements") or {}
        kwargs = {
            name: bool(requirements.get(name, False)) for name in _REQUIREMENT_FLAGS
        }
        return cls(**kwargs)

    @classmethod
    def from_run(cls, run: dict | None) -> ComputeRequirements:
        """Read requirements off a run record (``run['compute']['requirements']``)."""
        return cls._from_nested(run)

    @classmethod
    def from_agent(cls, agent: dict | None) -> ComputeRequirements:
        """Read requirements off an agent record (``agent['compute']['requirements']``)."""
        return cls._from_nested(agent)

    def as_flags(self) -> dict:
        """The flag map, JSON-safe, for persistence or logging."""
        return {name: getattr(self, name) for name in _REQUIREMENT_FLAGS}


@dataclass(frozen=True)
class ComputeAssignment:
    """The frozen result of a compute-routing decision.

    Records which provider was chosen, a human-readable reason naming why, and
    the requirements the decision was made from. ``as_record`` produces the
    JSON-safe dict a later phase persists on the run.
    """

    provider: ComputeProvider
    reason: str
    requirements: ComputeRequirements = field(default_factory=ComputeRequirements)

    def as_record(self) -> dict:
        """JSON-safe dict for persistence on the run record."""
        return {
            "provider": self.provider.value,
            "reason": self.reason,
            "requirements": self.requirements.as_flags(),
        }

    #: Alias mirroring the `to_dict` naming some callers may expect.
    def to_dict(self) -> dict:
        return self.as_record()
