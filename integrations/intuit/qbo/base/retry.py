# Python Standard Library Imports
import logging
import random
import time
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar

# Local Imports
from integrations.intuit.qbo.base.errors import QboError


logger = logging.getLogger(__name__)


T = TypeVar("T")

# Tier per-request timeout ceilings, mirroring client.py _TIMEOUT_TIERS
# (read/write + 5s connect + 5s margin). A single attempt can burn a full
# tier timeout before QBO answers.
TIER_A_REQUEST_CEILING_SECONDS: float = 40.0   # 30 read/write + 5 connect + 5 margin
TIER_B_REQUEST_CEILING_SECONDS: float = 70.0   # 60 read/write + 5 connect + 5 margin
TIER_C_REQUEST_CEILING_SECONDS: float = 130.0  # 120 read/write + 5 connect + 5 margin

_TIER_REQUEST_CEILING_SECONDS = {
    "A": TIER_A_REQUEST_CEILING_SECONDS,
    "B": TIER_B_REQUEST_CEILING_SECONDS,
    "C": TIER_C_REQUEST_CEILING_SECONDS,
}

# A budget derived from ALL configured max_attempts at full tier-timeout would be
# unsafe for for_reads (5 attempts x tier-A ceiling >= 200s) against the 240s App
# Service gateway timeout on inline admin sync pulls. Instead the budget guarantees
# a FLOOR of GUARANTEED_FULL_TIMEOUT_ATTEMPTS full-timeout attempts — NOT an exact
# count. Fast 429/503 failures can still use every configured attempt, and because
# the floor must also cover the largest possible Retry-After sleep (see below), a
# PURE back-to-back-timeout sequence (no Retry-After at all) can exceed the floor
# by one extra real attempt at the smaller tiers, where the leftover slack after
# one timeout + one max Retry-After sleep is still wide enough to admit another
# attempt. Verified by direct execution against this code (not estimated):
#   tier A: 3 real attempts, ~123s wall clock (floor states 2; actual behavior is
#           better — do not read "GUARANTEED_FULL_TIMEOUT_ATTEMPTS=2" as a promise
#           this never exceeds 2, only that it never goes BELOW 2)
#   tier B: 2 real attempts, ~141s wall clock
#   tier C: 2 real attempts, ~261s wall clock — EXCEEDS the 240s inline gateway
#           ceiling. Dormant today: no live get()/post()/put() caller selects
#           timeout_tier="C" (only post_multipart does, via the separate
#           for_uploads_single(), which is immune — max_attempts=1 means there is
#           no 2nd attempt to overrun). If a future caller ever selects tier C for
#           a retryable get/post/put, re-derive this budget first.
#
# The formula must cover a full-timeout attempt PLUS the largest possible single
# backoff sleep. Server-specified Retry-After bypasses jitter and is clamped to
# DEFAULT_MAX_RETRY_AFTER_CLAMP_SECONDS, so the budget adds that clamp — not just a
# small jitter margin — before BUDGET_SAFETY_MARGIN_SECONDS.
#
# All three tiers WILL exceed the scheduler's 60s soft-abandon timeout on the
# timeout-retry path — an accepted, deliberate tradeoff (abandonment only widens
# the window before the caller sees a result; the server-side call keeps running
# to completion and is not lost or corrupted). Tier A and B stay safely under the
# 240s hard gateway ceiling; tier C does not (see above).
GUARANTEED_FULL_TIMEOUT_ATTEMPTS: int = 2
DEFAULT_MAX_RETRY_AFTER_CLAMP_SECONDS: float = 60.0
BUDGET_SAFETY_MARGIN_SECONDS: float = 5.0


def _budget_for_tier(timeout_tier: str) -> float:
    try:
        ceiling = _TIER_REQUEST_CEILING_SECONDS[timeout_tier]
    except KeyError:
        raise ValueError(
            f"Unknown timeout_tier: {timeout_tier!r} (expected 'A', 'B', or 'C')"
        ) from None
    return (
        (GUARANTEED_FULL_TIMEOUT_ATTEMPTS - 1) * ceiling
        + DEFAULT_MAX_RETRY_AFTER_CLAMP_SECONDS
        + BUDGET_SAFETY_MARGIN_SECONDS
    )


@dataclass(frozen=True)
class RetryPolicy:
    """
    Retry policy for a single QBO HTTP call.

    Chapter 4 defaults:
      - writes: 3 attempts
      - reads:  5 attempts
      - 1s base, ×2 growth, full jitter
      - total budget derived per tier via `_budget_for_tier`, sized to guarantee
        AT LEAST `GUARANTEED_FULL_TIMEOUT_ATTEMPTS` full-timeout attempts (a floor,
        not an exact count — see the comment above that constant for the verified
        real attempt counts and wall-clock per tier); more attempts happen when
        failures are faster than a full timeout, and at tier A even a pure
        back-to-back-timeout sequence exceeds the floor by one
      - Retry-After header honored, clamped to 60s max

    Instances are immutable; construct via `for_writes()` / `for_reads()`
    or directly.
    """

    max_attempts: int
    base_backoff_seconds: float = 1.0
    backoff_multiplier: float = 2.0
    max_total_budget_seconds: float = 30.0
    max_retry_after_clamp_seconds: float = DEFAULT_MAX_RETRY_AFTER_CLAMP_SECONDS

    @classmethod
    def for_writes(cls, timeout_tier: str = "A") -> "RetryPolicy":
        return cls(max_attempts=3, max_total_budget_seconds=_budget_for_tier(timeout_tier))

    @classmethod
    def for_reads(cls, timeout_tier: str = "A") -> "RetryPolicy":
        return cls(max_attempts=5, max_total_budget_seconds=_budget_for_tier(timeout_tier))

    @classmethod
    def for_uploads_single(cls) -> "RetryPolicy":
        # QBO /upload multipart CREATE must be at-most-once. Retrying without an
        # idempotency token duplicates Attachables when attempt 1 already committed
        # (timeout, 503, or 2xx with empty/unparseable body are all POST-COMMIT-
        # AMBIGUOUS). Intuit does not document requestid on /upload, so we omit it.
        # max_attempts=1 means a failed upload here is NOT retried by anything above
        # this layer either — the outbox row retries the OUTER bill/entity push, and
        # the per-attachment catch in _sync_attachments_to_qbo (entities/bill/business/
        # service.py) stops an upload failure from ever reaching the outbox as a
        # retryable row. U-234: AttachableAttachmentConnector.sync_attachment_to_qbo
        # records a durable qbo.ReconciliationIssue (drift_type="attachment_upload_failed")
        # on failure so a dropped attachment is tracked, not silently lost.
        # 401-refresh-resend in _send_once is unchanged — 401 means not processed.
        max_total_budget_seconds = TIER_C_REQUEST_CEILING_SECONDS + 10.0
        return cls(max_attempts=1, max_total_budget_seconds=max_total_budget_seconds)


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

    Args:
        attempt: 1-indexed attempt number that just failed. The returned
                 sleep precedes attempt (attempt + 1).
        policy:  The active RetryPolicy.
        retry_after_seconds: Optional server-specified backoff value.

    Returns:
        Sleep duration in seconds (non-negative float).
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
    Execute `operation()` with retries on retryable QboErrors.

    Loops up to `policy.max_attempts` times, sleeping between attempts
    per `compute_backoff_seconds`. The total elapsed time (including
    sleeps) is capped at `policy.max_total_budget_seconds`; if a sleep
    would push us past the budget, we stop retrying and raise the last
    error.

    Non-retryable QboErrors (`is_retryable=False`) are raised immediately
    on first occurrence.

    Non-QboError exceptions propagate unchanged — the retry layer only
    knows how to classify the typed QBO hierarchy.

    Args:
        operation:      Zero-arg callable that either returns T or raises
                        a QboError. Typically a lambda that wraps a single
                        HTTP round-trip.
        policy:         Retry policy to apply.
        log:            Logger to emit structured retry events to. Defaults
                        to this module's logger.
        operation_name: Used in log fields; helps grep traces when one
                        request flows through many layers.
        correlation_id: Threaded into log fields for cross-system tracing.

    Returns:
        The value returned by a successful `operation()` call.

    Raises:
        QboError: The last error encountered when retries are exhausted,
                  the budget is spent, or the error was not retryable.
    """
    active_log = log or logger
    start_time = time.monotonic()
    last_error: Optional[QboError] = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            return operation()
        except QboError as error:
            last_error = error

            if not error.is_retryable:
                active_log.warning(
                    "qbo.retry.non_retryable",
                    extra={
                        "event_name": "qbo.retry.non_retryable",
                        "operation_name": operation_name,
                        "correlation_id": correlation_id,
                        "attempt": attempt,
                        "error_class": type(error).__name__,
                        "http_status": error.http_status,
                        "qbo_fault_code": error.code,
                    },
                )
                raise

            if attempt >= policy.max_attempts:
                active_log.error(
                    "qbo.retry.exhausted",
                    extra={
                        "event_name": "qbo.retry.exhausted",
                        "operation_name": operation_name,
                        "correlation_id": correlation_id,
                        "attempt": attempt,
                        "max_attempts": policy.max_attempts,
                        "error_class": type(error).__name__,
                        "http_status": error.http_status,
                        "qbo_fault_code": error.code,
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
                    "qbo.retry.budget_exceeded",
                    extra={
                        "event_name": "qbo.retry.budget_exceeded",
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
                "qbo.retry.scheduled",
                extra={
                    "event_name": "qbo.retry.scheduled",
                    "operation_name": operation_name,
                    "correlation_id": correlation_id,
                    "attempt": attempt,
                    "next_attempt": attempt + 1,
                    "sleep_seconds": sleep_seconds,
                    "error_class": type(error).__name__,
                    "http_status": error.http_status,
                    "qbo_fault_code": error.code,
                },
            )
            time.sleep(sleep_seconds)

    # Defensive: the loop always returns or raises; reaching here means
    # max_attempts was 0 or negative.
    if last_error is not None:
        raise last_error
    raise RuntimeError("execute_with_retry: no attempts were made (max_attempts must be >= 1)")
