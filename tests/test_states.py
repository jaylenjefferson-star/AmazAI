import pytest

from amazai.states import (
    PAUSED,
    SWEEPABLE,
    TERMINAL,
    InvalidTransition,
    RunState,
    can_transition,
    expected_pause_for,
    is_paused,
    is_terminal,
    transition,
)


class TestTerminalStates:
    @pytest.mark.parametrize("state", sorted(TERMINAL, key=lambda s: s.value))
    def test_terminal_states_are_absorbing(self, state):
        # Evidence is sealed on entry to a terminal state; moving again would
        # mean rewriting a sealed record.
        for target in RunState:
            assert not can_transition(state, target), f"{state} -> {target}"

    def test_expired_is_terminal(self):
        assert is_terminal(RunState.EXPIRED)

    def test_partial_is_terminal_and_distinct_from_failed(self):
        assert is_terminal(RunState.PARTIAL)
        assert RunState.PARTIAL is not RunState.FAILED


class TestPauseAndResume:
    @pytest.mark.parametrize("paused", sorted(PAUSED, key=lambda s: s.value))
    def test_every_pause_resumes_to_executing(self, paused):
        assert can_transition(paused, RunState.EXECUTING)

    @pytest.mark.parametrize("paused", sorted(PAUSED, key=lambda s: s.value))
    def test_every_pause_can_expire(self, paused):
        assert can_transition(paused, RunState.EXPIRED)

    def test_executing_can_reach_every_pause(self):
        for paused in PAUSED:
            assert can_transition(RunState.EXECUTING, paused)

    def test_approval_round_trip(self):
        s = RunState.EXECUTING
        s = transition(s, RunState.AWAITING_APPROVAL)
        s = transition(s, RunState.EXECUTING)
        s = transition(s, RunState.COMPLETED)
        assert is_terminal(s)

    def test_pause_kind_maps_to_state(self):
        assert expected_pause_for("approval") is RunState.AWAITING_APPROVAL
        assert expected_pause_for("login") is RunState.AWAITING_LOGIN

    def test_unknown_pause_kind_raises(self):
        with pytest.raises(ValueError):
            expected_pause_for("nonsense")


class TestCancellation:
    def test_cancelling_never_returns_to_executing(self):
        # The in-flight tool call finishes, then the run stops. Resuming would
        # mean a cancel that did not cancel.
        assert not can_transition(RunState.CANCELLING, RunState.EXECUTING)

    def test_cancelling_only_reaches_terminal_states(self):
        for target in RunState:
            if can_transition(RunState.CANCELLING, target):
                assert is_terminal(target), target


class TestSuspendResume:
    def test_suspended_resumes(self):
        # The 15-minute Lambda ceiling; Continue re-invokes the same session.
        assert can_transition(RunState.EXECUTING, RunState.SUSPENDED)
        assert can_transition(RunState.SUSPENDED, RunState.EXECUTING)


class TestInvalidTransitions:
    def test_raises_with_both_states_named(self):
        with pytest.raises(InvalidTransition) as exc:
            transition(RunState.COMPLETED, RunState.EXECUTING)
        assert exc.value.frm is RunState.COMPLETED
        assert exc.value.to is RunState.EXECUTING

    def test_queued_cannot_skip_to_executing(self):
        assert not can_transition(RunState.QUEUED, RunState.EXECUTING)


class TestSweeper:
    def test_sweepable_covers_all_non_terminal_states(self):
        assert SWEEPABLE == frozenset(RunState) - TERMINAL

    def test_no_state_is_both_paused_and_terminal(self):
        assert not (PAUSED & TERMINAL)
        assert all(is_paused(s) != is_terminal(s) or not is_paused(s) for s in RunState)
