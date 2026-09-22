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
