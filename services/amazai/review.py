"""Auto Review: the verdict on one step, in a form a person can read.

Every step a run takes is one of three things -- allowed, asked, or denied --
and for each the useful question is *why*. The answer already exists in
`policy.evaluate` (which rule fired) and in the router (whether the tool was
granted); this module turns them into one small record the console draws on the
step, and that is stored with it, so the trail says what was decided and by
which rule, not only that something happened.

Two things it must never do:

* **Decide.** It describes decisions made in `policy` and `router`. A verdict
  that could disagree with the gate it labels would be worse than none.
* **Claim what did not happen.** A tool that runs inside the harness sandbox is
  *observed* by the orchestrator, never gated by it (see the orchestrator
  docstring), and is labelled as such. Calling it "reviewed and allowed" would
  imply a check that did not run.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from amazai import policy

ALLOWED = "allowed"
ASKED = "asked"
DENIED = "denied"


@dataclass(frozen=True)
class Review:
    decision: str            # allowed | asked | denied
    rule: str                # a stable key: floor, capability, read, ...
    reason: str              # one short sentence
    matched: str = ""        # the pattern, capability class or approval id

    def to_dict(self) -> dict:
        return asdict(self)


def from_decision(d: policy.Decision) -> Review:
    """What `policy.evaluate` said, as a verdict."""
    return Review(ASKED if d.required else ALLOWED, d.rule, d.reason, d.matched)


def refused(exc: policy.Refused) -> Review:
    return Review(DENIED, "never", str(exc), getattr(exc, "matched", ""))


def ungranted(tool: str) -> Review:
    return Review(DENIED, "no_grant", f"{tool} has not been granted to this Bot", tool)


def approved(approval_id: str) -> Review:
    """A tool that needed approval and had one, bound to these arguments."""
    return Review(ALLOWED, "approved", "you approved these exact arguments", approval_id)


def sandbox(tool: str) -> Review:
    """A tool the harness runs itself. Observed, not gated."""
    return Review(ALLOWED, "sandbox", "runs in this Bot's own sandbox", tool)


def scoped(rule: str, reason: str) -> Review:
    """An inline function whose reach is fixed by its own definition (a handoff
    carries no access, `remember` writes only to this Bot)."""
    return Review(ALLOWED, rule, reason)
