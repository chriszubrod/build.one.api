# Python Standard Library Imports
import pytest

# Local Imports
from integrations.box.base import retry as retry_module
from integrations.box.base.retry import RetryPolicy, execute_with_retry, TIER_C_REQUEST_CEILING_SECONDS
from integrations.box.base.errors import BoxServiceUnavailableError


class _FailThenSucceed:
    """Callable that raises a retryable BoxError the first N calls, then returns."""

    def __init__(self, fail_times: int):
        self.calls = 0
        self.fail_times = fail_times

    def __call__(self):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise BoxServiceUnavailableError(
                'Box 503 unavailable',
                http_status=503,
                retry_after_seconds=None,
            )
        return {'ok': True}


@pytest.fixture
def deterministic_retry(monkeypatch):
    """Freeze randomness + sleep, and make attempt 1 'consume' a full tier-C timeout.

    monotonic() returns 1000.0 on the first call (start_time) then 1000.0 + 120.0
    on every subsequent call, so elapsed reads as a full 120s tier-C attempt.
    """
    calls = {'n': 0}

    def fake_monotonic():
        calls['n'] += 1
        return 1000.0 if calls['n'] == 1 else 1000.0 + 120.0

    monkeypatch.setattr(retry_module.time, 'monotonic', fake_monotonic)
    monkeypatch.setattr(retry_module.time, 'sleep', lambda _s: None)
    monkeypatch.setattr(retry_module.random, 'uniform', lambda _a, _b: 0.01)
    return calls


def test_for_downloads_budget_satisfies_invariant():
    policy = RetryPolicy.for_downloads()
    # The budget must fit at least max_attempts full tier-C timeout attempts.
    assert policy.max_total_budget_seconds >= policy.max_attempts * TIER_C_REQUEST_CEILING_SECONDS
    assert policy.max_attempts >= 2


def test_for_downloads_retries_after_full_timeout_attempt(deterministic_retry):
    # Attempt 1 'consumes' a full 120s tier-C timeout then 503s; for_downloads()'s
    # large budget must still allow attempt 2, which succeeds.
    op = _FailThenSucceed(fail_times=1)
    result = execute_with_retry(op, RetryPolicy.for_downloads(), operation_name='box.file.download')
    assert result == {'ok': True}
    assert op.calls == 2


def test_for_reads_budget_exceeds_on_same_scenario(deterministic_retry):
    # The SAME slow-then-503 scenario under the old for_reads() 30s budget
    # budget-exceeds on attempt 1 -> raises with ZERO retries. This is the bug.
    # for_reads() allows multiple attempts by policy, so the single call proves
    # the BUDGET aborted the retry (not the attempt ceiling) -- the real branch.
    assert RetryPolicy.for_reads().max_attempts >= 2
    op = _FailThenSucceed(fail_times=1)
    with pytest.raises(BoxServiceUnavailableError):
        execute_with_retry(op, RetryPolicy.for_reads(), operation_name='box.file.download')
    assert op.calls == 1
