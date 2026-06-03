"""Tests for the retry decorator."""

import time

import pytest

from strata.exceptions import PermanentError, TransientError
from strata.retry import retry


class TestRetry:
    def test_returns_on_success_first_try(self):
        calls = {"n": 0}

        @retry(max_attempts=3, base_delay_s=0.01)
        def f():
            calls["n"] += 1
            return "ok"

        assert f() == "ok"
        assert calls["n"] == 1

    def test_retries_on_transient_error(self):
        calls = {"n": 0}

        @retry(max_attempts=3, base_delay_s=0.01)
        def f():
            calls["n"] += 1
            if calls["n"] < 3:
                raise TransientError("nope")
            return "ok"

        assert f() == "ok"
        assert calls["n"] == 3

    def test_raises_after_max_attempts(self):
        calls = {"n": 0}

        @retry(max_attempts=3, base_delay_s=0.01)
        def f():
            calls["n"] += 1
            raise TransientError("always fails")

        with pytest.raises(TransientError):
            f()
        assert calls["n"] == 3

    def test_does_not_retry_permanent(self):
        calls = {"n": 0}

        @retry(max_attempts=3, base_delay_s=0.01)
        def f():
            calls["n"] += 1
            raise PermanentError("don't retry")

        with pytest.raises(PermanentError):
            f()
        assert calls["n"] == 1

    def test_backoff_applies_delay(self):
        @retry(max_attempts=3, base_delay_s=0.05, max_delay_s=1.0)
        def f():
            raise TransientError("retry me")

        start = time.monotonic()
        with pytest.raises(TransientError):
            f()
        elapsed = time.monotonic() - start
        # At least one backoff interval (~0.025s minimum with jitter)
        assert elapsed >= 0.02
