import pytest

from amazai.errors import MAX_REPLANS, ErrorClass, classify


class TestTransient:
    @pytest.mark.parametrize("msg", [
        "ThrottlingException: Rate exceeded",
        "connection reset by peer",
        "HTTP 503 Service Unavailable",
        "request timed out",
    ])
    def test_recognised(self, msg):
        assert classify(msg).cls is ErrorClass.TRANSIENT

    def test_backoff_grows(self):
        a = classify("throttled", attempt=0).backoff_seconds
        b = classify("throttled", attempt=1).backoff_seconds
        assert b > a

    def test_gives_up_after_three(self):
        c = classify("throttled", attempt=3)
        assert c.cls is ErrorClass.TERMINAL
        assert not c.retryable


class TestNeedsHuman:
    @pytest.mark.parametrize("msg", [
        "HTTP 401 Unauthorized",
        "403 Forbidden",
        "MFA required",
        "captcha challenge presented",
        "invalid_grant: token expired",
    ])
    def test_pauses_rather_than_retrying(self, msg):
        c = classify(msg)
        assert c.cls is ErrorClass.NEEDS_HUMAN
        assert not c.retryable


class TestTerminal:
    @pytest.mark.parametrize("msg", [
        "grant denied for pr.merge",
        "budget exceeded",
        "invalid target repository",
    ])
    def test_never_retried(self, msg):
        c = classify(msg)
        assert c.cls is ErrorClass.TERMINAL
        assert not c.retryable


class TestPrecedence:
    def test_403_is_not_treated_as_transient(self):
        # A 403 retried as a throttle walks straight into a lockout.
        c = classify("403 Forbidden: rate limit context")
        assert c.cls is ErrorClass.NEEDS_HUMAN

    def test_terminal_beats_transient(self):
        c = classify("budget exceeded after timeout")
        assert c.cls is ErrorClass.TERMINAL


class TestReplan:
    def test_test_failure_is_replannable(self):
        assert classify("1 test failed").cls is ErrorClass.NEEDS_REPLAN

    def test_gives_up_after_max_replans(self):
        assert classify("validation error", attempt=MAX_REPLANS).cls is ErrorClass.TERMINAL

    def test_still_replannable_just_under_the_bound(self):
        assert classify("validation error", attempt=MAX_REPLANS - 1).cls is ErrorClass.NEEDS_REPLAN


class TestUnknownErrors:
    def test_gets_exactly_one_replan(self):
        assert classify("something strange", attempt=0).cls is ErrorClass.NEEDS_REPLAN

    def test_then_fails_rather_than_looping(self):
        # Defaulting unknown errors to "retry" turns a mystery into a bill.
        assert classify("something strange", attempt=1).cls is ErrorClass.TERMINAL
