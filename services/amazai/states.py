"""The run state machine.

Implements `docs/architecture/05-run-lifecycle.md`. Transitions are validated,
not merely documented: an invalid transition raises rather than silently
corrupting a run's recorded history.
"""

from __future__ import annotations

from enum import Enum


class RunState(str, Enum):
    QUEUED = "QUEUED"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    AWAITING_INPUT = "AWAITING_INPUT"
    AWAITING_LOGIN = "AWAITING_LOGIN"
    AWAITING_CONNECTOR = "AWAITING_CONNECTOR"
    RETRYING = "RETRYING"
    SUSPENDED = "SUSPENDED"
    CANCELLING = "CANCELLING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


#: Terminal states. Evidence is sealed on entry and the run never moves again.
TERMINAL: frozenset[RunState] = frozenset({
    RunState.COMPLETED,
    RunState.PARTIAL,
    RunState.FAILED,
    RunState.CANCELLED,
    RunState.EXPIRED,
})

#: States where the run is parked on something outside the system. Nothing is
#: held open and nothing is billed while a run sits here; the whole resumable
#: state is the RUN# row plus the session ID.
PAUSED: frozenset[RunState] = frozenset({
    RunState.AWAITING_APPROVAL,
    RunState.AWAITING_INPUT,
    RunState.AWAITING_LOGIN,
    RunState.AWAITING_CONNECTOR,
})

#: States the sweeper inspects for a stale heartbeat or a passed deadline.
#: Every non-terminal state belongs here. SUSPENDED in particular: a run that
#: hit the 15-minute ceiling and was never continued would otherwise sit
#: forever, invisible to recovery.
SWEEPABLE: frozenset[RunState] = frozenset({
    RunState.QUEUED,
    RunState.PLANNING,
    RunState.EXECUTING,
    RunState.RETRYING,
    RunState.SUSPENDED,
    RunState.CANCELLING,
}) | PAUSED

_ANY_TERMINAL = frozenset(TERMINAL)

_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.QUEUED: frozenset({RunState.PLANNING, RunState.CANCELLING}) | _ANY_TERMINAL,
    RunState.PLANNING: frozenset({RunState.EXECUTING, RunState.CANCELLING}) | _ANY_TERMINAL,
    RunState.EXECUTING: frozenset({
        RunState.RETRYING,
        RunState.SUSPENDED,
        RunState.CANCELLING,
    }) | PAUSED | _ANY_TERMINAL,
    # A pause resumes straight into EXECUTING once the decision or input lands.
    RunState.AWAITING_APPROVAL: frozenset({RunState.EXECUTING, RunState.CANCELLING}) | _ANY_TERMINAL,
    RunState.AWAITING_INPUT: frozenset({RunState.EXECUTING, RunState.CANCELLING}) | _ANY_TERMINAL,
    RunState.AWAITING_LOGIN: frozenset({RunState.EXECUTING, RunState.CANCELLING}) | _ANY_TERMINAL,
    RunState.AWAITING_CONNECTOR: frozenset({RunState.EXECUTING, RunState.CANCELLING}) | _ANY_TERMINAL,
    RunState.RETRYING: frozenset({RunState.EXECUTING, RunState.CANCELLING}) | _ANY_TERMINAL,
    RunState.SUSPENDED: frozenset({RunState.EXECUTING, RunState.CANCELLING}) | _ANY_TERMINAL,
    # Cancellation lets the in-flight tool call finish, so CANCELLING can only
    # settle into a terminal state -- never back into EXECUTING.
    RunState.CANCELLING: frozenset(_ANY_TERMINAL),
    # Terminal states are absorbing.
    RunState.COMPLETED: frozenset(),
    RunState.PARTIAL: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.CANCELLED: frozenset(),
    RunState.EXPIRED: frozenset(),
}


class InvalidTransition(RuntimeError):
    def __init__(self, frm: RunState, to: RunState) -> None:
        super().__init__(f"illegal run transition {frm.value} -> {to.value}")
        self.frm = frm
        self.to = to


def is_terminal(state: RunState) -> bool:
    return state in TERMINAL


def is_paused(state: RunState) -> bool:
    return state in PAUSED


def can_transition(frm: RunState, to: RunState) -> bool:
    return to in _TRANSITIONS[frm]


def transition(frm: RunState, to: RunState) -> RunState:
    """Return `to`, or raise if the move is not legal."""
    if not can_transition(frm, to):
        raise InvalidTransition(frm, to)
    return to


#: What a pause resumes on. Used by the API to reject, say, an approval
#: decision posted against a run that is waiting on a login instead.
RESUME_TRIGGER: dict[RunState, str] = {
    RunState.AWAITING_APPROVAL: "approval",
    RunState.AWAITING_INPUT: "input",
    RunState.AWAITING_LOGIN: "login",
    RunState.AWAITING_CONNECTOR: "connector",
}


def expected_pause_for(kind: str) -> RunState:
    for state, trigger in RESUME_TRIGGER.items():
        if trigger == kind:
            return state
    raise ValueError(f"unknown pause kind: {kind}")
