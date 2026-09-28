# Python Standard Library Imports
import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar

# Local Imports
from integrations.ramp.base.errors import RampError, RampRateLimitError
from integrations.ramp.base.logger import get_ramp_logger


logger = get_ramp_logger(__name__)

T = TypeVar("T")

# Ramp docs: exponential backoff 1s → 2s → 4s on 429; never immediate retry.
_RAMP_RATE_LIMIT_BACKOFF_SECONDS = (1.0, 2.0, 4.0)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    base_backoff_seconds: float = 1.0
    backoff_multiplier: float = 2.0
    max_total_budget_seconds: float = 60.0
    max_retry_after_clamp_seconds: float = 60.0
    use_ramp_rate_limit_schedule: bool = False

    @classmethod
    def for_reads(cls) -> "RetryPolicy":
        return cls(max_attempts=5, max_total_budget_seconds=120.0)

    @classmethod
    def for_ramp_rate_limit(cls) -> "RetryPolicy":
        return cls(
            max_attempts=4,
            max_total_budget_seconds=120.0,
            use_ramp_rate_limit_schedule=True,
        )


def compute_backoff_seconds(
    attempt: int,
    policy: RetryPolicy,
    *,
    error: Optional[RampError] = None,
    retry_after_seconds: Optional[float] = None,
) -> float:
    if isinstance(error, RampRateLimitError):
        idx = min(max(0, attempt - 1), len(_RAMP_RATE_LIMIT_BACKOFF_SECONDS) - 1)
        return _RAMP_RATE_LIMIT_BACKOFF_SECONDS[idx]

    if retry_after_seconds is not None and retry_after_seconds > 0:
        return min(retry_after_seconds, policy.max_retry_after_clamp_seconds)

    computed = policy.base_backoff_seconds * (policy.backoff_multiplier ** max(0, attempt - 1))
    return computed


def execute_with_retry(
    operation: Callable[[], T],
    policy: RetryPolicy,
    *,
    log: Optional[logging.Logger] = None,
    operation_name: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> T:
    active_log = log or logger
    start_time = time.monotonic()
    last_error: Optional[RampError] = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            return operation()
        except RampError as error:
            last_error = error

            if not error.is_retryable:
                raise

            if attempt >= policy.max_attempts:
                raise

            sleep_seconds = compute_backoff_seconds(
                attempt=attempt,
                policy=policy,
                error=error,
                retry_after_seconds=error.retry_after_seconds,
            )

            elapsed = time.monotonic() - start_time
            remaining_budget = policy.max_total_budget_seconds - elapsed
            if sleep_seconds >= remaining_budget and remaining_budget <= 0:
                raise

            active_log.info(
                "ramp.retry.scheduled",
                extra={
                    "event_name": "ramp.retry.scheduled",
                    "operation_name": operation_name,
                    "attempt": attempt,
                    "sleep_seconds": sleep_seconds,
                    "error_class": type(error).__name__,
                },
            )
            time.sleep(sleep_seconds)

    if last_error is not None:
        raise last_error
    raise RuntimeError("execute_with_retry: no attempts were made")
