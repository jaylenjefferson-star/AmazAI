"""Tool-path routing and tool resolution.

Implements `docs/architecture/05-run-lifecycle.md`. The model proposes a plan;
this module decides what it is actually handed.

`resolve_tools` is the load-bearing function. A tool the agent may not use is
ABSENT from the schema the model sees -- not present and refused. An absent
tool cannot be argued for, injected into, or retried into existence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ToolPath(str, Enum):
    """Ordered narrowest-first. Lower `rank` wins when several could serve."""
    CONNECTOR_API = "connector_api"
    WORKSPACE_TERMINAL = "workspace_terminal"
    CODING_JOB = "coding_job"
    CLOUD_BROWSER = "cloud_browser"
    ANOTHER_AGENT = "another_agent"
    ROUTINE_EVENT = "routine_event"
    LOCAL_COMPANION = "local_companion"


RANK: dict[ToolPath, int] = {
    ToolPath.CONNECTOR_API: 1,
    ToolPath.WORKSPACE_TERMINAL: 2,
    ToolPath.CODING_JOB: 3,
    ToolPath.CLOUD_BROWSER: 4,
    ToolPath.ANOTHER_AGENT: 5,
    ToolPath.ROUTINE_EVENT: 6,
    ToolPath.LOCAL_COMPANION: 7,
}

#: Not implemented until M5. Listed so the router already has a slot for it.
UNAVAILABLE: frozenset[ToolPath] = frozenset({ToolPath.LOCAL_COMPANION})

#: Built-in tools that are on by default in create_harness and must not be
#: declared. Listed here so resolution can reason about them.
BUILTIN_DEFAULT: frozenset[str] = frozenset({"shell", "file_operations"})


class PathUnavailable(RuntimeError):
    pass


@dataclass
class Grant:
    connector_id: str
    allowed_tools: frozenset[str]
    capability: str = "read"


@dataclass
class ResolutionInput:
    """Everything resolution needs. No model output appears here."""
    agent_allowed_tools: frozenset[str]
    grants: list[Grant] = field(default_factory=list)
    #: True when a granted connector tool can produce the requested outcome.
    #: Set by the caller from the goal classification plus the grant list.
    connector_covers_outcome: bool = False
    over_budget_tools: frozenset[str] = frozenset()
    rate_limited_tools: frozenset[str] = frozenset()
    #: Restricts the browser to these hosts, for routines that only need one site.
    browser_domain_allowlist: tuple[str, ...] = ()


@dataclass(frozen=True)
class Resolution:
    tools: tuple[str, ...]
    connector_tools: tuple[str, ...]
    suppressed: tuple[str, ...]
    notes: tuple[str, ...]


def granted_tools(grants: list[Grant]) -> frozenset[str]:
    out: set[str] = set()
    for g in grants:
        out |= set(g.allowed_tools)
    return frozenset(out)


def resolve_tools(inp: ResolutionInput) -> Resolution:
    """Compute the effective tool list for one run.

    grants -> minus browser when a connector covers it -> minus over-budget
    -> minus rate-limited.
    """
    notes: list[str] = []
    suppressed: set[str] = set()

    builtin = set(inp.agent_allowed_tools)
    connector = set(granted_tools(inp.grants))

    # Rule 4 from docs/architecture/05: never screen-scrape what a scoped API
    # can do. Enforced by removal, not by prompting.
    if inp.connector_covers_outcome and "browser" in builtin:
        builtin.discard("browser")
        suppressed.add("browser")
        notes.append("browser suppressed: a granted connector covers this outcome")

    for pool, label in ((builtin, "builtin"), (connector, "connector")):
        for tool in sorted(pool & set(inp.over_budget_tools)):
            pool.discard(tool)
            suppressed.add(tool)
            notes.append(f"{tool} suppressed: over budget ({label})")
        for tool in sorted(pool & set(inp.rate_limited_tools)):
            pool.discard(tool)
            suppressed.add(tool)
            notes.append(f"{tool} suppressed: rate limited ({label})")

    if "browser" in builtin and inp.browser_domain_allowlist:
        notes.append(
            "browser restricted to: " + ", ".join(inp.browser_domain_allowlist)
        )

    return Resolution(
        tools=tuple(sorted(builtin | connector)),
        connector_tools=tuple(sorted(connector)),
        suppressed=tuple(sorted(suppressed)),
        notes=tuple(notes),
    )


def select_path(candidates: set[ToolPath]) -> ToolPath:
    """Pick the narrowest available path from the candidates."""
    usable = {c for c in candidates if c not in UNAVAILABLE}
    if not usable:
        raise PathUnavailable(
            "no usable tool path; "
            + ", ".join(sorted(c.value for c in candidates))
            + " unavailable"
        )
    return min(usable, key=lambda c: RANK[c])


def may_call(tool: str, resolution: Resolution) -> bool:
    """Second gate, at execution time.

    Resolution already removed the tool from the schema, so reaching here with
    a disallowed tool means something went wrong upstream. Checking twice is
    cheap; a missed check is a boundary violation.
    """
    return tool in resolution.tools or tool in BUILTIN_DEFAULT
