# Python Standard Library Imports
import pytest

# Local Imports
from integrations.intuit.qbo.base import retry as retry_module
from integrations.intuit.qbo.base.retry import (
    RetryPolicy,
    execute_with_retry,
    TIER_A_REQUEST_CEILING_SECONDS,
    TIER_B_REQUEST_CEILING_SECONDS,
    TIER_C_REQUEST_CEILING_SECONDS,
    GUARANTEED_FULL_TIMEOUT_ATTEMPTS,
    DEFAULT_MAX_RETRY_AFTER_CLAMP_SECONDS,
    BUDGET_SAFETY_MARGIN_SECONDS,
)
from integrations.intuit.qbo.base.client import _TIMEOUT_TIERS
from integrations.intuit.qbo.base.errors import QboServiceUnavailableError


class _FailThenSucceed:
    """Callable that raises a retryable QboError the first N calls, then returns."""

    def __init__(self, fail_times: int, retry_after_seconds=None):
        self.calls = 0
        self.fail_times = fail_times
        self.retry_after_seconds = retry_after_seconds

    def __call__(self):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise QboServiceUnavailableError(
                "QBO 503 unavailable",
                http_status=503,
                retry_after_seconds=self.retry_after_seconds,
            )
        return {"ok": True}


def _deterministic_every_attempt_full_timeout_clock(monkeypatch, ceiling_seconds):
    """Each failed attempt 'consumes' a full tier timeout before the next retry."""
    calls = {"n": 0}

    def fake_monotonic():
        calls["n"] += 1
        return 1000.0 + (calls["n"] - 1) * ceiling_seconds

    monkeypatch.setattr(retry_module.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(retry_module.time, "sleep", lambda _s: None)
    monkeypatch.setattr(retry_module.random, "uniform", lambda _a, _b: 0.01)
    return calls


def _deterministic_instant_failures_with_sleep_advance(monkeypatch):
    """Near-instant failures; monotonic advances by the sleep duration."""
    clock = {"t": 1000.0}

    def fake_monotonic():
        return clock["t"]

    def fake_sleep(seconds):
        clock["t"] += seconds

    monkeypatch.setattr(retry_module.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(retry_module.time, "sleep", fake_sleep)
    monkeypatch.setattr(retry_module.random, "uniform", lambda _a, _b: 0.01)


def test_for_writes_survives_one_full_tier_a_timeout_attempt(monkeypatch):
    _deterministic_every_attempt_full_timeout_clock(monkeypatch, TIER_A_REQUEST_CEILING_SECONDS)
    op = _FailThenSucceed(fail_times=1)
    result = execute_with_retry(op, RetryPolicy.for_writes(), operation_name="qbo.bill.push")
    assert result == {"ok": True}
    assert op.calls == 2


def test_for_reads_survives_one_full_tier_a_timeout_attempt(monkeypatch):
    _deterministic_every_attempt_full_timeout_clock(monkeypatch, TIER_A_REQUEST_CEILING_SECONDS)
    op = _FailThenSucceed(fail_times=1)
    result = execute_with_retry(op, RetryPolicy.for_reads(), operation_name="qbo.bill.pull")
    assert result == {"ok": True}
    assert op.calls == 2


def test_for_reads_budget_exceeds_when_every_attempt_is_full_timeout(monkeypatch):
    _deterministic_every_attempt_full_timeout_clock(monkeypatch, TIER_A_REQUEST_CEILING_SECONDS)
    op = _FailThenSucceed(fail_times=99)
    with pytest.raises(QboServiceUnavailableError):
        execute_with_retry(op, RetryPolicy.for_reads(), operation_name="qbo.bill.pull")
    assert op.calls >= 2
    assert op.calls < 5


def test_for_reads_survives_retry_after_near_old_budget(monkeypatch):
    _deterministic_instant_failures_with_sleep_advance(monkeypatch)
    op = _FailThenSucceed(fail_times=1, retry_after_seconds=55)
    result = execute_with_retry(op, RetryPolicy.for_reads(), operation_name="qbo.bill.pull")
    assert result == {"ok": True}
    assert op.calls == 2


def test_for_reads_completes_all_attempts_with_moderate_retry_after(monkeypatch):
    _deterministic_instant_failures_with_sleep_advance(monkeypatch)
    op = _FailThenSucceed(fail_times=4, retry_after_seconds=20)
    result = execute_with_retry(op, RetryPolicy.for_reads(), operation_name="qbo.bill.pull")
    assert result == {"ok": True}
    assert op.calls == 5


def test_budget_derivation_matches_tier(monkeypatch):
    for tier, ceiling in (
        ("A", TIER_A_REQUEST_CEILING_SECONDS),
        ("B", TIER_B_REQUEST_CEILING_SECONDS),
        ("C", TIER_C_REQUEST_CEILING_SECONDS),
    ):
        expected = (
            (GUARANTEED_FULL_TIMEOUT_ATTEMPTS - 1) * ceiling
            + DEFAULT_MAX_RETRY_AFTER_CLAMP_SECONDS
            + BUDGET_SAFETY_MARGIN_SECONDS
        )
        assert RetryPolicy.for_writes(timeout_tier=tier).max_total_budget_seconds == expected
        assert RetryPolicy.for_reads(timeout_tier=tier).max_total_budget_seconds == expected


def test_tier_ceilings_match_client_timeout_tiers():
    # retry.py's TIER_*_REQUEST_CEILING_SECONDS is a hand-maintained mirror of
    # client.py's _TIMEOUT_TIERS (retry.py can't import client.py directly:
    # client.py already imports RetryPolicy from retry.py, so the reverse
    # import would be circular). This test is the coupling check that keeps
    # the two tables from silently drifting apart -- exactly the failure mode
    # this unit exists to fix, just at the level of two constants instead of
    # one budget number.
    margin_seconds = 5.0
    for tier, ceiling in (
        ("A", TIER_A_REQUEST_CEILING_SECONDS),
        ("B", TIER_B_REQUEST_CEILING_SECONDS),
        ("C", TIER_C_REQUEST_CEILING_SECONDS),
    ):
        timeout = _TIMEOUT_TIERS[tier]
        assert timeout.read == timeout.write
        assert ceiling == timeout.read + timeout.connect + margin_seconds


def test_unknown_tier_raises_value_error():
    with pytest.raises(ValueError):
        RetryPolicy.for_writes(timeout_tier="Z")
    with pytest.raises(ValueError):
        RetryPolicy.for_reads(timeout_tier="Z")


def test_for_writes_default_tier_is_a():
    assert RetryPolicy.for_writes().max_total_budget_seconds == RetryPolicy.for_writes(timeout_tier="A").max_total_budget_seconds


# Regression guard against ACCIDENTAL changes to the untouched sibling method — not a test of this unit's fix.
def test_for_uploads_single_unaffected_by_this_fix():
    policy = RetryPolicy.for_uploads_single()
    assert policy.max_attempts == 1
    assert policy.max_total_budget_seconds == TIER_C_REQUEST_CEILING_SECONDS + 10.0
