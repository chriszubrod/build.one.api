"""Process-local Claude spend cap, out-of-credit breaker, and spend recording.

Each gunicorn worker holds its own breaker state and spend cache — caps are
best-effort per process, not cluster-wide.

Spend is persisted in ``dbo.LlmDailySpend`` via ``LlmSpendRepository``; DB
read failures fail closed (treat day as capped) after a stale grace window.
Write failures keep spend in a per-UTC-day ``_unsaved_by_date`` buffer for retry.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Callable, Optional, Protocol

from config import Settings
from intelligence.observability.pricing import compute_cost_usd
from intelligence.transport.base import Usage

logger = logging.getLogger(__name__)

ANTHROPIC_PROVIDER = "anthropic"
_DEFAULT_CAP_USD = Decimal("6.45")
_CONSERVATIVE_PRICE_MODEL = "claude-sonnet-5-5"
_READ_FAIL_CLOSED_SECONDS = 600.0
_LOG_RATE_LIMIT_SECONDS = 60.0
_spend_lock = threading.Lock()


class LlmSpendReader(Protocol):
    def read_daily_spend(self, spend_date: date, provider: str) -> Decimal: ...


class LlmSpendWriter(Protocol):
    def add_daily_spend(
        self, spend_date: date, provider: str, cost_usd: Decimal,
    ) -> Decimal: ...


@dataclass
class _SpendCache:
    spend_date: date
    total_usd: Decimal
    fetched_at: float


_blocked_until: Optional[datetime] = None
_credit_warning_logged_for_block: bool = False
_spend_cache: Optional[_SpendCache] = None
_unsaved_by_date: dict[date, Decimal] = {}
_inflight_by_date: dict[date, Decimal] = {}
_unsaved_spend: Decimal = Decimal("0")  # sum mirror (tests / probes)
_unpriced_model_warnings: set[str] = set()
_fail_closed_until_read: bool = False
_last_read_fail_log_at: float = 0.0
_last_write_fail_log_at: float = 0.0
_no_key_warning_logged: bool = False

_now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
_repo: Optional[LlmSpendReader] = None


def configure_for_tests(
    *,
    now_fn: Optional[Callable[[], datetime]] = None,
    repo_reader: Optional[LlmSpendReader] = None,
    repo_writer: Optional[LlmSpendWriter] = None,
) -> None:
    """Reset process state and inject fakes (tests only)."""
    global _blocked_until, _credit_warning_logged_for_block, _spend_cache
    global _unsaved_by_date, _inflight_by_date, _unsaved_spend, _unpriced_model_warnings, _fail_closed_until_read
    global _last_read_fail_log_at, _last_write_fail_log_at, _no_key_warning_logged
    global _now_fn, _repo
    _blocked_until = None
    _credit_warning_logged_for_block = False
    _spend_cache = None
    _unsaved_by_date = {}
    _inflight_by_date = {}
    _invalid_setting_warnings.clear()
    _unsaved_spend = Decimal("0")
    _unpriced_model_warnings = set()
    _fail_closed_until_read = False
    _last_read_fail_log_at = 0.0
    _last_write_fail_log_at = 0.0
    _no_key_warning_logged = False
    _now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    if repo_reader is not None or repo_writer is not None:
        _repo = _TestRepoShim(repo_reader, repo_writer)
    else:
        _repo = None


class _TestRepoShim:
    """Single fake repo for tests (reader + writer may share state)."""

    def __init__(
        self,
        reader: Optional[LlmSpendReader],
        writer: Optional[LlmSpendWriter],
    ) -> None:
        self._reader = reader
        self._writer = writer

    def read_daily_spend(self, spend_date: date, provider: str) -> Decimal:
        assert self._reader is not None
        return self._reader.read_daily_spend(spend_date, provider)

    def add_daily_spend(
        self, spend_date: date, provider: str, cost_usd: Decimal,
    ) -> Decimal:
        assert self._writer is not None
        out = self._writer.add_daily_spend(spend_date, provider, cost_usd)
        if isinstance(out, Decimal):
            return out
        return Decimal("0")


class _ZeroSpendRepo:
    """Pytest default when no repo is injected — avoids live DB reads in unit tests."""

    def read_daily_spend(self, spend_date: date, provider: str) -> Decimal:
        return Decimal("0")

    def add_daily_spend(
        self, spend_date: date, provider: str, cost_usd: Decimal,
    ) -> Decimal:
        return cost_usd


def _maybe_autouse_pytest_repo() -> None:
    global _repo
    if _repo is not None:
        return
    if "pytest" not in sys.modules or not os.environ.get("PYTEST_CURRENT_TEST"):
        return
    _repo = _ZeroSpendRepo()


def _get_repo() -> LlmSpendReader:
    global _repo
    _maybe_autouse_pytest_repo()
    if _repo is None:
        from shared.llm_spend_repo import LlmSpendRepository

        _repo = LlmSpendRepository()
    return _repo


def _utc_today() -> date:
    return _now_fn().date()


def _daily_cap_usd(settings: Settings) -> Decimal:
    raw = settings.claude_daily_spend_cap_usd
    if raw is None or (isinstance(raw, str) and not str(raw).strip()):
        return _DEFAULT_CAP_USD
    try:
        cap = Decimal(str(raw).strip())
    except Exception:
        logger.warning("invalid CLAUDE_DAILY_SPEND_CAP_USD=%r; using default", raw)
        return _DEFAULT_CAP_USD
    if not cap.is_finite() or cap < 0:
        logger.warning("invalid CLAUDE_DAILY_SPEND_CAP_USD=%r; using default", raw)
        return _DEFAULT_CAP_USD
    return cap


_DEFAULT_CACHE_SECONDS = 30.0
_DEFAULT_BREAKER_MINUTES = 60
_MAX_BREAKER_MINUTES = 7 * 24 * 60
_invalid_setting_warnings: set[tuple[str, str]] = set()


def _warn_invalid_setting_once(name: str, raw: object, default: object) -> None:
    key = (name, repr(raw))
    if key not in _invalid_setting_warnings:
        _invalid_setting_warnings.add(key)
        logger.warning("invalid %s=%r; using default %s", name, raw, default)


def _cache_ttl_seconds(settings: Settings) -> float:
    raw = settings.claude_spend_cache_seconds
    if raw is None or not str(raw).strip():
        return _DEFAULT_CACHE_SECONDS
    try:
        value = float(str(raw).strip())
    except ValueError:
        value = float("nan")
    if not math.isfinite(value) or value < 0:
        _warn_invalid_setting_once(
            "CLAUDE_SPEND_CACHE_SECONDS", raw, _DEFAULT_CACHE_SECONDS,
        )
        return _DEFAULT_CACHE_SECONDS
    return value  # 0 = no caching (read every call)


def _breaker_minutes(settings: Settings) -> int:
    raw = settings.claude_credit_breaker_minutes
    if raw is None or not str(raw).strip():
        return _DEFAULT_BREAKER_MINUTES
    try:
        value = int(str(raw).strip())
    except ValueError:
        value = 0
    if value <= 0 or value > _MAX_BREAKER_MINUTES:
        _warn_invalid_setting_once(
            "CLAUDE_CREDIT_BREAKER_MINUTES", raw, _DEFAULT_BREAKER_MINUTES,
        )
        return _DEFAULT_BREAKER_MINUTES
    return value


def is_credit_breaker_open() -> bool:
    if _blocked_until is None:
        return False
    return _now_fn() < _blocked_until


def open_credit_breaker(settings: Optional[Settings] = None) -> None:
    global _blocked_until, _credit_warning_logged_for_block
    settings = settings or Settings()
    now = _now_fn()
    was_active = _blocked_until is not None and now < _blocked_until
    if not was_active:
        _credit_warning_logged_for_block = False
    minutes = _breaker_minutes(settings)
    _blocked_until = datetime.fromtimestamp(
        now.timestamp() + minutes * 60, tz=timezone.utc,
    )
    if not _credit_warning_logged_for_block:
        logger.warning("claude credit exhausted, falling back to Foundry")
        _credit_warning_logged_for_block = True


def _is_credit_exhausted_error(error: Optional[str]) -> bool:
    if not error:
        return False
    lower = error.lower()
    if "http_402" in lower or "billing_error" in lower:
        return True
    if "http_400" in lower and "credit balance" in lower:
        return True
    return False


def _unsaved_for(spend_date: date) -> Decimal:
    # In-flight writes count too: until their total comes back they are spend
    # the cache does not hold yet. Callers hold ``_spend_lock``.
    return _unsaved_by_date.get(spend_date, Decimal("0")) + _inflight_by_date.get(
        spend_date, Decimal("0"),
    )


def _effective_db_total(spend_date: date) -> Decimal:
    base = Decimal("0")
    if _spend_cache is not None and _spend_cache.spend_date == spend_date:
        base = _spend_cache.total_usd
    return base + _unsaved_for(spend_date)


def _effective_total_locked(spend_date: date) -> Decimal:
    """Effective spend for a reader that does not hold ``_spend_lock``.

    Writers move amounts between cache, unsaved and in-flight in both
    directions, so no unlocked read order is safe; take the lock (it is never
    held across DB I/O)."""
    with _spend_lock:
        return _effective_db_total(spend_date)


def _rate_limited_warning(last_at: float, *, kind: str, exc: Exception) -> float:
    now = time.monotonic()
    if now - last_at >= _LOG_RATE_LIMIT_SECONDS:
        logger.warning("LlmDailySpend %s failed: %s", kind, type(exc).__name__)
        return now
    return last_at


def _sync_unsaved_mirror() -> None:
    global _unsaved_spend
    _unsaved_spend = sum(_unsaved_by_date.values(), Decimal("0")) + sum(
        _inflight_by_date.values(), Decimal("0"),
    )


def _store_db_total(spend_date: date, total: Decimal) -> None:
    """Refresh the cache from a DB total (call under ``_spend_lock``).

    LlmDailySpend only ever increments, so for the same day the larger total is
    the newer one: a read that started before a concurrent write committed must
    not lower the cache below the total that write returned."""
    global _spend_cache
    if (
        _spend_cache is not None
        and _spend_cache.spend_date == spend_date
        and _spend_cache.total_usd > total
    ):
        total = _spend_cache.total_usd
    _spend_cache = _SpendCache(
        spend_date=spend_date, total_usd=total, fetched_at=time.monotonic(),
    )


def _read_spend_sync(spend_date: date, settings: Settings) -> Decimal:
    global _spend_cache, _fail_closed_until_read, _last_read_fail_log_at
    now = time.monotonic()
    if (
        _spend_cache is not None
        and _spend_cache.spend_date == spend_date
        and (now - _spend_cache.fetched_at) <= _cache_ttl_seconds(settings)
    ):
        return _effective_total_locked(spend_date)

    try:
        total = _get_repo().read_daily_spend(spend_date, ANTHROPIC_PROVIDER)
    except Exception as exc:
        _last_read_fail_log_at = _rate_limited_warning(
            _last_read_fail_log_at, kind="read", exc=exc,
        )
        if (
            _spend_cache is not None
            and _spend_cache.spend_date == spend_date
            and (now - _spend_cache.fetched_at) <= _READ_FAIL_CLOSED_SECONDS
        ):
            return _effective_total_locked(spend_date)
        _fail_closed_until_read = True
        return _effective_total_locked(spend_date)

    with _spend_lock:
        _fail_closed_until_read = False
        _store_db_total(spend_date, total)
        return _effective_db_total(spend_date)


def get_today_anthropic_spend() -> Decimal:
    settings = Settings()
    return _read_spend_sync(_utc_today(), settings)


def is_claude_day_stopped() -> bool:
    """True when daily cap is reached or spend read is fail-closed."""
    settings = Settings()
    cap = _daily_cap_usd(settings)
    if cap <= 0:
        return False
    spend = _read_spend_sync(_utc_today(), settings)
    if _fail_closed_until_read:
        return True
    return spend >= cap


async def is_claude_day_stopped_async() -> bool:
    """Async wrapper — DB read off the event loop (bill text tick)."""
    return await asyncio.to_thread(is_claude_day_stopped)


def _usage_cost_usd(model: str, usage: Usage) -> Optional[Decimal]:
    if not usage.input_tokens and not usage.output_tokens and not (
        usage.cache_creation_input_tokens or usage.cache_read_input_tokens
    ):
        return None
    cost_f = compute_cost_usd(provider=ANTHROPIC_PROVIDER, model=model, usage=usage)
    if cost_f is None:
        if model not in _unpriced_model_warnings:
            logger.warning(
                "no pricing for anthropic model %s; metering at Sonnet 5.5 rates",
                model,
            )
            _unpriced_model_warnings.add(model)
        cost_f = compute_cost_usd(
            provider=ANTHROPIC_PROVIDER,
            model=_CONSERVATIVE_PRICE_MODEL,
            usage=usage,
        )
    if cost_f is None:
        return None
    return Decimal(f"{cost_f:.6f}")


def _record_spend_sync(cost_usd: Decimal, settings: Settings) -> None:
    """Add ``cost_usd`` to today and persist every day's unsaved spend.

    Each write moves its amount from ``_unsaved_by_date`` to ``_inflight_by_date``
    under the lock, writes WITHOUT the lock (a hung DB must not queue every
    metering thread), then settles under the lock: success drops the in-flight
    amount, failure returns it to unsaved for that same SpendDate. Concurrent
    writers therefore persist disjoint amounts — nothing is double-counted.
    Unsaved spend from an earlier UTC day is retried under ITS date."""
    global _last_write_fail_log_at
    if cost_usd <= 0:
        return
    today = _utc_today()
    with _spend_lock:
        _unsaved_by_date[today] = _unsaved_by_date.get(today, Decimal("0")) + cost_usd
        batch = sorted(_unsaved_by_date.items())
        for spend_date, amount in batch:
            _inflight_by_date[spend_date] = (
                _inflight_by_date.get(spend_date, Decimal("0")) + amount
            )
        _unsaved_by_date.clear()
        _sync_unsaved_mirror()

    for spend_date, amount in batch:
        try:
            new_total = _get_repo().add_daily_spend(
                spend_date, ANTHROPIC_PROVIDER, amount,
            )
        except Exception as exc:
            with _spend_lock:
                _unsaved_by_date[spend_date] = (
                    _unsaved_by_date.get(spend_date, Decimal("0")) + amount
                )
                _settle_inflight(spend_date, amount)
                _last_write_fail_log_at = _rate_limited_warning(
                    _last_write_fail_log_at, kind="write", exc=exc,
                )
                _sync_unsaved_mirror()
            continue
        with _spend_lock:
            _store_db_total(spend_date, new_total)
            _settle_inflight(spend_date, amount)
            _sync_unsaved_mirror()


def _settle_inflight(spend_date: date, amount: Decimal) -> None:
    remaining = _inflight_by_date.get(spend_date, Decimal("0")) - amount
    if remaining > 0:
        _inflight_by_date[spend_date] = remaining
    else:
        _inflight_by_date.pop(spend_date, None)


def record_anthropic_spend(cost_usd: Decimal) -> None:
    _record_spend_sync(cost_usd, Settings())


def finalize_after_attempt(
    *,
    usage: Usage,
    model: str,
    error: Optional[str],
    settings: Optional[Settings] = None,
) -> None:
    """Post-call metering (sync): credit breaker + spend whenever usage is non-zero."""
    settings = settings or Settings()
    if _is_credit_exhausted_error(error):
        open_credit_breaker(settings)
    cost = _usage_cost_usd(model, usage)
    if cost is not None and cost > 0:
        _record_spend_sync(cost, settings)


def warn_no_api_key_once() -> None:
    global _no_key_warning_logged
    if not _no_key_warning_logged:
        logger.warning("ANTHROPIC_API_KEY is not configured")
        _no_key_warning_logged = True


async def pre_call_gate(*, has_api_key: bool) -> Optional[str]:
    """Return skip token (disabled/credit/daily_cap/no_key) or None if call may proceed."""
    settings = Settings()
    cap = _daily_cap_usd(settings)
    if cap <= 0:
        return "disabled"
    if not has_api_key:
        warn_no_api_key_once()
        return "no_key"
    if is_credit_breaker_open():
        return "credit"
    spend = await asyncio.to_thread(_read_spend_sync, _utc_today(), settings)
    if _fail_closed_until_read or spend >= cap:
        return "daily_cap"
    return None


async def post_call_metering(
    *,
    usage: Usage,
    model: str,
    error: Optional[str],
) -> None:
    settings = Settings()
    await asyncio.to_thread(
        finalize_after_attempt,
        usage=usage,
        model=model,
        error=error,
        settings=settings,
    )
