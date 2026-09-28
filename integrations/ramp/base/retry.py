# Python Standard Library Imports
import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar

# Local Imports
from integrations.ramp.base.errors import RampError
from integrations.ramp.base.logger import get_ramp_logger


logger = get_ramp_logger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    base_backoff_seconds: float = 1.0
    backoff_multiplier: float = 2.0
    max_total_budget_seconds: float = 60.0
    max_retry_after_clamp_seconds: float = 60.0

    @classmethod
    def for_reads(cls) -> "RetryPolicy":
        return cls(max_attempts=5, max_total_budget_seconds=120.0)

    @classmethod
    def for_token_mint(cls) -> "RetryPolicy":
        return cls(max_attempts=4, max_total_budget_seconds=120.0)


def compute_backoff_seconds(
    attempt: int,
    policy: RetryPolicy,
    *,
    error: Optional[RampError] = None,
) -> float:
    floor_schedule = type(error).backoff_floor_seconds if error is not None else ()
    if floor_schedule:
        idx = min(max(0, attempt - 1), len(floor_schedule) - 1)
        fixed = floor_schedule[idx]
        retry_after_seconds = error.retry_after_seconds if error is not None else None
        if retry_after_seconds is not None and retry_after_seconds > 0:
            wait = max(fixed, retry_after_seconds)
            return min(wait, policy.max_retry_after_clamp_seconds)
        return fixed

    retry_after_seconds = error.retry_after_seconds if error is not None else None
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
            )

            elapsed = time.monotonic() - start_time
            remaining_budget = policy.max_total_budget_seconds - elapsed
            if remaining_budget <= 0:
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
