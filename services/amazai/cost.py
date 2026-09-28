"""Cost ledger and budget enforcement.

Implements the three-way ledger in `docs/architecture/03-data-model.md`.
Written from day one even though billing does not exist, because
reconstructing per-run cost after the fact is impossible.

Budget behaviour is D7 in docs/architecture/15-open-decisions.md. Both modes
are implemented; `on_ceiling` on the agent or routine selects one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

WARN_FRACTION = 0.8


class Verdict(str, Enum):
    OK = "ok"
    WARN = "warn"
    STOP = "stop"


def model_usd(model_id: str, *, input_tokens: int = 0, output_tokens: int = 0,
              cached_tokens: int | None = None) -> float:
    """What one model call cost, in dollars.

    Deliberately delegates to `usage.PRICING`. There is one price table in
    this codebase and this is the seam to it: a second table here would drift
    from that one, and the first symptom of drift is a budget that stops runs
    at the wrong number.

    `usage` works in micro-dollar integers to keep a month's arithmetic exact;
    this ledger is floats because it predates that decision. The conversion
    happens here, once, rather than at every call site.
    """
    from amazai.usage import MICRO, TokenCounts, cost_micros
    micros = cost_micros(model_id, TokenCounts(
        input=input_tokens, output=output_tokens, cached=cached_tokens))
    return micros / MICRO


@dataclass
class RunCost:
    model_usd: float = 0.0
    runtime_usd: float = 0.0
    connector_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    model_calls: int = 0
    runtime_seconds: float = 0.0
    connector_calls: dict[str, int] = field(default_factory=dict)

    @property
    def total_usd(self) -> float:
        return round(self.model_usd + self.runtime_usd + self.connector_usd, 6)

    def add_model(self, usd: float, *, input_tokens: int = 0, output_tokens: int = 0,
                  cached_tokens: int = 0) -> None:
        self.model_usd += usd
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cached_tokens += cached_tokens
        self.model_calls += 1

    def add_runtime(self, usd: float, *, seconds: float = 0.0) -> None:
        self.runtime_usd += usd
        self.runtime_seconds += seconds

    def add_connector(self, provider: str, usd: float = 0.0, *, calls: int = 1) -> None:
        self.connector_usd += usd
        self.connector_calls[provider] = self.connector_calls.get(provider, 0) + calls

    def to_item(self) -> dict:
        return {
            "modelUsd": round(self.model_usd, 6),
            "runtimeUsd": round(self.runtime_usd, 6),
            "connectorUsd": round(self.connector_usd, 6),
            "totalUsd": self.total_usd,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "cachedTokens": self.cached_tokens,
            "modelCalls": self.model_calls,
            "runtimeSeconds": round(self.runtime_seconds, 3),
            "connectorCalls": dict(self.connector_calls),
        }


@dataclass(frozen=True)
class Budget:
    per_run_usd: float
    per_month_usd: float
    on_ceiling: str = "hard_stop"          # hard_stop | warn
    max_tool_calls_per_run: int = 60
    max_tool_errors_per_run: int = 6
    max_consecutive_tool_errors: int = 3


@dataclass(frozen=True)
class BudgetCheck:
    verdict: Verdict
    reason: str
    run_fraction: float
    month_fraction: float

    @property
    def should_stop(self) -> bool:
        return self.verdict is Verdict.STOP


@dataclass(frozen=True)
class TaskBudget:
    """A per-task envelope carved out of a coordinator's own run budget.

    A fan-out is one logical piece of work spread across a coordinator and the
    children it spawns. Left to the flat `Budget.per_run_usd` alone, each child
    would get the coordinator's whole per-run ceiling, so a task with five
    children could spend six times what a single run may -- the account credit
    ceiling in `billing.py` still bounds the total, but nothing between here and
    that hard cap kept one fan-out proportional to one run's worth of spend.

    This composes that missing middle: the coordinator's `per_run_usd` is the
    envelope for the whole task, and `for_child` splits it across the spawned
    children so their shares sum to (at most) the envelope. It is a ceiling, not
    a reservation -- a child that finishes cheaply does not hand its slack to a
    sibling -- and it never grants more than the agent's own `Budget` already
    allows, so it can only tighten enforcement, never loosen it.

    Scaffolding, not live enforcement. This is a pure composition helper: it
    computes a proportional per-task envelope, and nothing more. It is
    intentionally NOT wired into the live run loop. This runtime deliberately
    removed per-agent and per-run budget gating in favour of a single
    account-level credit ceiling (`billing.has_credit`; see the notes around the
    credit check in services/handlers/orchestrator.py and in collab.py), so
    reintroducing a per-run gate on the spawn path would be a regression against
    that design decision. What this class provides is the number a FUTURE
    account-level or opt-in per-task check could consume; the "only narrows,
    never grants" property above describes the math, not a guarantee that any
    live run is being bounded by it today. A reader must not mistake it for
    active enforcement -- spend is bounded at runtime by the account credit
    ceiling, not by this envelope.
    """
    envelope_usd: float
    child_count: int
    coordinator_reserve: float = 0.2  # coordinator keeps a slice for its own synthesis turn

    @property
    def coordinator_usd(self) -> float:
        """The coordinator's own share -- what it may spend on planning and the
        final synthesis run, held back before children are allotted."""
        if self.child_count <= 0:
            return round(self.envelope_usd, 6)
        return round(self.envelope_usd * self.coordinator_reserve, 6)

    def for_child(self, *, agent_budget: Budget | None = None) -> float:
        """One child's dollar ceiling: an equal share of what remains after the
        coordinator's reserve, split across the spawned children. Never exceeds
        the child agent's own per-run ceiling, so a cheap-agent child is still
        capped by its own `Budget` and the composition only ever narrows."""
        if self.child_count <= 0:
            return 0.0
        share = round((self.envelope_usd - self.coordinator_usd) / self.child_count, 6)
        if agent_budget is not None:
            return round(min(share, agent_budget.per_run_usd), 6)
        return share


def compose_task_budget(coordinator_budget: Budget, *, child_count: int) -> TaskBudget:
    """Build the per-task envelope from the coordinator's agent `Budget`.

    The envelope is the coordinator's own `per_run_usd`: one fan-out is treated
    as one run's worth of spend regardless of how many children it splits into.
    This is the only place the split ratio is decided, so there is one number to
    reason about rather than per-child limits scattered across the spawn path.

    Like `TaskBudget`, this is pure composition and scaffolding: it returns an
    envelope for a future account-level or opt-in per-task check to consume. It
    is intentionally not called on the live spawn path, where spend is bounded
    by the account credit ceiling (`billing.has_credit`), not by per-run
    budgets. Calling it does not enforce anything on its own.
    """
    return TaskBudget(envelope_usd=coordinator_budget.per_run_usd,
                      child_count=max(0, int(child_count)))


def budget_for_agent(agent: dict) -> Budget:
    """The one place an agent row's raw `budget` dict becomes a `Budget`.

    Shared by the orchestrator's own run loop and `collab.may_wake_now`, so a
    priority-woken recipient is checked against exactly the same ceilings a
    normally-triggered run would be -- not a second, looser copy of them.
    """
    b = agent.get("budget", {}) or {}
    return Budget(
        per_run_usd=float(b.get("perRunUsd", 2.0)),
        per_month_usd=float(b.get("perMonthUsd", 40.0)),
        on_ceiling=b.get("onCeiling", "hard_stop"),
        max_tool_calls_per_run=int(b.get("maxToolCallsPerRun", 60)),
    )


def spent_this_month(store, agent_id: str) -> float:
    from amazai import keys as K
    from amazai.store import now_iso
    month = now_iso()[:7]
    rows = store.query(K.cost_pk(store.owner_id, agent_id, month), limit=500)
    return sum(float(r.get("totalUsd", 0.0)) for r in rows)


def check(
    budget: Budget,
    *,
    spent_this_run: float,
    spent_this_month: float,
    tool_calls: int = 0,
    tool_errors: int = 0,
    consecutive_tool_errors: int = 0,
) -> BudgetCheck:
    """Evaluate every numeric ceiling for a run.

    Ceilings are numbers checked by code rather than guidance in a system
    prompt, because a retry loop is the most likely way to spend real money by
    accident.
    """
    run_frac = spent_this_run / budget.per_run_usd if budget.per_run_usd else 0.0
    month_frac = spent_this_month / budget.per_month_usd if budget.per_month_usd else 0.0

    # Non-monetary ceilings are hard regardless of on_ceiling: they exist to
    # stop a loop, and "warn and keep looping" defeats the purpose.
    if tool_calls >= budget.max_tool_calls_per_run:
        return BudgetCheck(Verdict.STOP, "tool call ceiling reached", run_frac, month_frac)
    if tool_errors >= budget.max_tool_errors_per_run:
        return BudgetCheck(Verdict.STOP, "tool error ceiling reached", run_frac, month_frac)
    if consecutive_tool_errors >= budget.max_consecutive_tool_errors:
        return BudgetCheck(Verdict.STOP, "too many consecutive errors on one tool", run_frac, month_frac)

    over_run = run_frac >= 1.0
    over_month = month_frac >= 1.0
    if over_run or over_month:
        which = "per-run" if over_run else "monthly"
        if budget.on_ceiling == "warn":
            return BudgetCheck(Verdict.WARN, f"{which} budget exceeded (warn mode)", run_frac, month_frac)
        return BudgetCheck(Verdict.STOP, f"{which} budget exceeded", run_frac, month_frac)

    if run_frac >= WARN_FRACTION or month_frac >= WARN_FRACTION:
        return BudgetCheck(Verdict.WARN, "approaching budget ceiling", run_frac, month_frac)

    return BudgetCheck(Verdict.OK, "within budget", run_frac, month_frac)
