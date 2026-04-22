# Python Standard Library Imports
import logging
import random
import time
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar

# Local Imports
from integrations.ms.base.errors import MsGraphError


logger = logging.getLogger(__name__)


T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    """
    Retry policy for a single MS Graph HTTP call.

    Defaults mirror the QBO Chapter 4 policy:
      - writes: 3 attempts
      - reads:  5 attempts
      - 1s base, ×2 growth, full jitter
      - 30s per-request total budget (including sleeps)
      - Retry-After header honored, clamped to 60s max

    Instances are immutable; construct via `for_writes()` / `for_reads()`
    or directly.
    """

    max_attempts: int
    base_backoff_seconds: float = 1.0
    backoff_multiplier: float = 2.0
    max_total_budget_seconds: float = 30.0
    max_retry_after_clamp_seconds: float = 60.0

    @classmethod
    def for_writes(cls) -> "RetryPolicy":
        return cls(max_attempts=3)

    @classmethod
    def for_reads(cls) -> "RetryPolicy":
        return cls(max_attempts=5)


def compute_backoff_seconds(
    attempt: int,
    policy: RetryPolicy,
    retry_after_seconds: Optional[float] = None,
) -> float:
    """
    Compute the sleep duration before the next retry attempt.

    If `retry_after_seconds` is provided (typically from a Retry-After
    header on 429/503), that value wins — clamped to policy's max. This
    respects the server's explicit backoff guidance over our computed
    exponential value.

    Otherwise, uses exponential backoff with full jitter:
        actual_sleep = uniform(0, base * multiplier ** (attempt - 1))

    Full jitter prevents thundering herd when many clients retry
    simultaneously after a shared failure.
    """
    if retry_after_seconds is not None and retry_after_seconds > 0:
        return min(retry_after_seconds, policy.max_retry_after_clamp_seconds)

    computed = policy.base_backoff_seconds * (policy.backoff_multiplier ** max(0, attempt - 1))
    return random.uniform(0, computed)


def execute_with_retry(
    operation: Callable[[], T],
    policy: RetryPolicy,
    *,
    log: Optional[logging.Logger] = None,
    operation_name: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> T:
    """
    Execute `operation()` with retries on retryable MsGraphErrors.

    Loops up to `policy.max_attempts` times, sleeping between attempts
    per `compute_backoff_seconds`. The total elapsed time (including
    sleeps) is capped at `policy.max_total_budget_seconds`; if a sleep
    would push us past the budget, we stop retrying and raise the last
    error.

    Non-retryable MsGraphErrors (`is_retryable=False`) are raised
    immediately on first occurrence.

    Non-MsGraphError exceptions propagate unchanged — the retry layer
    only knows how to classify the typed MS hierarchy.
    """
    active_log = log or logger
    start_time = time.monotonic()
    last_error: Optional[MsGraphError] = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            return operation()
        except MsGraphError as error:
            last_error = error

            if not error.is_retryable:
                active_log.warning(
                    "ms.retry.non_retryable",
                    extra={
                        "event_name": "ms.retry.non_retryable",
                        "operation_name": operation_name,
                        "correlation_id": correlation_id,
                        "attempt": attempt,
                        "error_class": type(error).__name__,
                        "http_status": error.http_status,
                        "ms_error_code": error.code,
                    },
                )
                raise

            if attempt >= policy.max_attempts:
                active_log.error(
                    "ms.retry.exhausted",
                    extra={
                        "event_name": "ms.retry.exhausted",
                        "operation_name": operation_name,
                        "correlation_id": correlation_id,
                        "attempt": attempt,
                        "max_attempts": policy.max_attempts,
                        "error_class": type(error).__name__,
                        "http_status": error.http_status,
                        "ms_error_code": error.code,
                    },
                )
                raise

            sleep_seconds = compute_backoff_seconds(
                attempt=attempt,
                policy=policy,
                retry_after_seconds=error.retry_after_seconds,
            )

            elapsed = time.monotonic() - start_time
            remaining_budget = policy.max_total_budget_seconds - elapsed
            if sleep_seconds >= remaining_budget:
                active_log.error(
                    "ms.retry.budget_exceeded",
                    extra={
                        "event_name": "ms.retry.budget_exceeded",
                        "operation_name": operation_name,
                        "correlation_id": correlation_id,
                        "attempt": attempt,
                        "elapsed_seconds": elapsed,
                        "remaining_budget_seconds": max(0.0, remaining_budget),
                        "planned_sleep_seconds": sleep_seconds,
                        "error_class": type(error).__name__,
                    },
                )
                raise

            active_log.info(
                "ms.retry.scheduled",
                extra={
                    "event_name": "ms.retry.scheduled",
                    "operation_name": operation_name,
                    "correlation_id": correlation_id,
                    "attempt": attempt,
                    "next_attempt": attempt + 1,
                    "sleep_seconds": sleep_seconds,
                    "error_class": type(error).__name__,
                    "http_status": error.http_status,
                    "ms_error_code": error.code,
                },
            )
            time.sleep(sleep_seconds)

    if last_error is not None:
        raise last_error
    raise RuntimeError("execute_with_retry: no attempts were made (max_attempts must be >= 1)")
