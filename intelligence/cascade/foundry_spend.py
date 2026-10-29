"""U-701d: Foundry (OpenAI-compatible surface) spend metering, alert, and opt-in cap.

Mirrors ``claude_budget``'s per-UTC-day buffer algorithm with its OWN lock and
state: every Foundry call's cost lands in an unsaved bucket in memory, then a
dedicated single daemon thread flushes it to ``dbo.LlmDailySpend`` under
``Provider='foundry'``. Each flush moves the batch unsaved -> in-flight under
the lock, writes WITHOUT the lock, then settles: success drops the in-flight
amount, failure returns it to unsaved under its ORIGINAL SpendDate (retried on
the next flush, across UTC midnight). Concurrent flushes therefore persist
disjoint amounts — nothing is double-counted.

Every buffered amount is bound to the repo it was recorded against (and to
whether it may alert), so a queued flush can never write a harness-ledger
batch to the live repo or the reverse. Toggling claude_budget's isolated
harness mode calls ``reset_process_state()``, which drops this module's
buffers, cache and alert state.

Claude-on-Foundry goes through ``AnthropicTransport`` and is metered as
``anthropic`` by ``claude_budget``; this module only meters ``FoundryTransport``
(DeepSeek / GPT-5.4 family), so nothing is counted twice.

``record_usage`` is called synchronously from the transport and never awaits
the DB or raises. The repo comes from ``claude_budget.get_spend_repo()`` so the
pytest zero-spend repo, test injection and U-702 isolated ledger all apply.

Alert: ``FOUNDRY_SPEND_ALERT_USD`` (default 2.00, ``0`` disables) logs
``llm.spend.foundry_threshold_crossed`` when a write returns a DB total at or
above it — at most once per worker process per UTC day (again after a
restart). An App Insights log alert pages on that line.

Cap: ``FOUNDRY_DAILY_SPEND_CAP_USD`` is OFF by default — unset / empty / ``0``
means NO cap (unlike ``CLAUDE_DAILY_SPEND_CAP_USD``, where ``0`` disables
Claude). A cap fails OPEN on a read error. Per-process, best-effort: each
worker sees its own unsaved spend plus the shared DB total, so concurrent
workers may overshoot.

Settings are parsed once and refreshed every ``_SETTINGS_REFRESH_SECONDS``.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

from config import Settings
from intelligence.cascade import claude_budget
from intelligence.observability.pricing import compute_cost_usd
from intelligence.transport.base import Usage

logger = logging.getLogger(__name__)

FOUNDRY_PROVIDER = "foundry"
ALERT_EVENT_NAME = "llm.spend.foundry_threshold_crossed"
_DEFAULT_ALERT_USD = Decimal("2.00")
_CONSERVATIVE_PRICE_MODEL = "gpt-5.4-mini"
_LOG_RATE_LIMIT_SECONDS = 60.0
_SETTINGS_REFRESH_SECONDS = 60.0

_lock = threading.Lock()

# A buffered amount's identity: (repo it was recorded against, may it alert, UTC day).
_Bucket = tuple  # (repo, bool, date)


@dataclass
class _SpendCache:
    spend_date: date
    total_usd: Decimal
    fetched_at: float


@dataclass(frozen=True)
class _FoundrySettings:
    alert_usd: Optional[Decimal]
    cap_usd: Optional[Decimal]
    cache_ttl_seconds: float
    loaded_at: float


_unsaved: dict[_Bucket, Decimal] = {}
_inflight: dict[_Bucket, Decimal] = {}
_spend_cache: Optional[_SpendCache] = None
_alerted_dates: set[date] = set()
_unpriced_model_warnings: set[str] = set()
_invalid_setting_warnings: set[tuple[str, str]] = set()
_flush_pending: bool = False
_generation: int = 0
_settings_cache: Optional[_FoundrySettings] = None
_last_record_fail_log_at: float = 0.0
_last_write_fail_log_at: float = 0.0
_last_read_fail_log_at: float = 0.0

_now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
_executor: Any = None


class _DaemonExecutor:
    """One daemon worker thread draining a queue. A daemon so a hung
    AddLlmDailySpend can never hold up interpreter shutdown (a
    ThreadPoolExecutor's workers are joined at exit)."""

    def __init__(self, name: str = "foundry-spend") -> None:
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self.thread = threading.Thread(target=self._run, name=name, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while True:
            fn = self._queue.get()
            try:
                fn()
            except Exception:  # pragma: no cover - _flush never raises
                logger.warning("foundry spend worker task failed", exc_info=True)

    def submit(self, fn: Callable[[], Any]) -> None:
        self._queue.put(fn)


class _SyncExecutor:
    """Runs submitted work inline (tests only) — makes flushes deterministic."""

    def submit(self, fn: Callable[[], Any]) -> None:
        fn()


def _clear_state_locked() -> None:
    global _unsaved, _inflight, _spend_cache, _alerted_dates, _flush_pending, _generation
    _unsaved = {}
    _inflight = {}
    _spend_cache = None
    _alerted_dates = set()
    _flush_pending = False
    _generation += 1


def reset_process_state() -> None:
    """Drop buffered spend, cached totals and alert state. Called by
    ``claude_budget`` whenever its harness ledger is switched on or off, so
    nothing recorded under one ledger is counted, gated or flushed under the
    other. A flush already mid-write settles nothing after a reset."""
    with _lock:
        _clear_state_locked()


def configure_for_tests(
    *,
    now_fn: Optional[Callable[[], datetime]] = None,
    executor: Any = "sync",
) -> None:
    """Reset process state (tests only). ``executor="sync"`` (default) runs
    flushes inline; pass an object with ``submit(fn)`` to exercise real
    threading, or ``None`` to restore the lazily-created daemon worker."""
    global _unpriced_model_warnings, _settings_cache
    global _last_record_fail_log_at, _last_write_fail_log_at, _last_read_fail_log_at
    global _now_fn, _executor
    with _lock:
        _clear_state_locked()
        _unpriced_model_warnings = set()
        _invalid_setting_warnings.clear()
        _settings_cache = None
        _last_record_fail_log_at = 0.0
        _last_write_fail_log_at = 0.0
        _last_read_fail_log_at = 0.0
    _now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    _executor = _SyncExecutor() if executor == "sync" else executor


def _get_executor() -> Any:
    global _executor
    if _executor is None:
        with _lock:
            if _executor is None:
                _executor = _DaemonExecutor()
    return _executor


def _is_isolated() -> bool:
    """U-702 harness mode is active (the isolated ledger answers per provider)."""
    return claude_budget.get_isolated_spend_total(FOUNDRY_PROVIDER) is not None


def _utc_today() -> date:
    return _now_fn().date()


def _rate_limited_warning(last_at: float, *, kind: str, exc: BaseException) -> float:
    now = time.monotonic()
    if now - last_at >= _LOG_RATE_LIMIT_SECONDS:
        logger.warning("Foundry LlmDailySpend %s failed: %s", kind, type(exc).__name__)
        return now
    return last_at


def _warn_invalid_setting_once(name: str, raw: object, default: object) -> None:
    key = (name, repr(raw))
    if key not in _invalid_setting_warnings:
        _invalid_setting_warnings.add(key)
        logger.warning("invalid %s=%r; using %s", name, raw, default)


def _parse_usd(raw: object) -> Optional[Decimal]:
    try:
        value = Decimal(str(raw).strip())
    except Exception:
        return None
    if not value.is_finite() or value < 0:
        return None
    return value


def alert_threshold_usd(settings: Settings) -> Optional[Decimal]:
    """Daily alert threshold; None = alert disabled (``0``)."""
    raw = settings.foundry_spend_alert_usd
    if raw is None or not str(raw).strip():
        return _DEFAULT_ALERT_USD
    value = _parse_usd(raw)
    if value is None:
        _warn_invalid_setting_once("FOUNDRY_SPEND_ALERT_USD", raw, _DEFAULT_ALERT_USD)
        return _DEFAULT_ALERT_USD
    return value if value > 0 else None


def daily_cap_usd(settings: Settings) -> Optional[Decimal]:
    """Daily Foundry cap; None = no cap (unset / empty / ``0`` / invalid)."""
    raw = settings.foundry_daily_spend_cap_usd
    if raw is None or not str(raw).strip():
        return None
    value = _parse_usd(raw)
    if value is None:
        _warn_invalid_setting_once("FOUNDRY_DAILY_SPEND_CAP_USD", raw, "no cap")
        return None
    return value if value > 0 else None


def _settings() -> _FoundrySettings:
    """Parsed Foundry settings, built once and refreshed every
    ``_SETTINGS_REFRESH_SECONDS`` — not a ``Settings()`` per call."""
    global _settings_cache
    cached = _settings_cache
    now = time.monotonic()
    if cached is not None and now - cached.loaded_at < _SETTINGS_REFRESH_SECONDS:
        return cached
    s = Settings()
    cached = _FoundrySettings(
        alert_usd=alert_threshold_usd(s),
        cap_usd=daily_cap_usd(s),
        cache_ttl_seconds=claude_budget._cache_ttl_seconds(s),
        loaded_at=now,
    )
    _settings_cache = cached
    return cached


def usage_cost_usd(model: str, usage: Usage) -> Optional[Decimal]:
    """Dollar cost of one Foundry call; None for zero usage (nothing to write)."""
    if not usage.input_tokens and not usage.output_tokens and not (
        usage.cache_creation_input_tokens or usage.cache_read_input_tokens
    ):
        return None
    cost_f = compute_cost_usd(provider=FOUNDRY_PROVIDER, model=model, usage=usage)
    if cost_f is None:
        if model not in _unpriced_model_warnings:
            _unpriced_model_warnings.add(model)
            logger.warning(
                "no pricing for foundry model %s; metering at %s rates",
                model, _CONSERVATIVE_PRICE_MODEL,
            )
        cost_f = compute_cost_usd(
            provider=FOUNDRY_PROVIDER, model=_CONSERVATIVE_PRICE_MODEL, usage=usage,
        )
    if cost_f is None:
        return None
    return Decimal(f"{cost_f:.6f}")


def record_usage(model: str, usage: Usage) -> None:
    """Meter one finished Foundry call. Memory-only on the caller's thread; the
    DB write is scheduled on the dedicated worker. Never raises."""
    global _flush_pending, _last_record_fail_log_at
    try:
        cost = usage_cost_usd(model, usage)
        if cost is None or cost <= 0:
            return
        # Bind the amount NOW to the repo and alert eligibility in force for
        # this call (test injection / harness ledger / live).
        bucket = (claude_budget.get_spend_repo(), not _is_isolated(), _utc_today())
        submit = False
        with _lock:
            _unsaved[bucket] = _unsaved.get(bucket, Decimal("0")) + cost
            if not _flush_pending:
                _flush_pending = True
                submit = True
        if submit:
            try:
                _get_executor().submit(_flush)
            except Exception:
                with _lock:
                    _flush_pending = False
                raise
    except Exception as exc:
        _last_record_fail_log_at = _rate_limited_warning(
            _last_record_fail_log_at, kind="record", exc=exc,
        )


def _settle_inflight(bucket: _Bucket, amount: Decimal) -> None:
    remaining = _inflight.get(bucket, Decimal("0")) - amount
    if remaining > 0:
        _inflight[bucket] = remaining
    else:
        _inflight.pop(bucket, None)


def _store_db_total(spend_date: date, total: Decimal) -> None:
    """Cache a DB total (call under ``_lock``); keep the larger for the same day —
    LlmDailySpend only increments, so a smaller total is an older read."""
    global _spend_cache
    if (
        _spend_cache is not None
        and _spend_cache.spend_date == spend_date
        and _spend_cache.total_usd > total
    ):
        total = _spend_cache.total_usd
    _spend_cache = _SpendCache(spend_date=spend_date, total_usd=total, fetched_at=time.monotonic())


def _flush() -> None:
    """Persist every buffered bucket to ITS OWN repo (worker thread). Never raises."""
    global _flush_pending, _last_write_fail_log_at
    try:
        with _lock:
            _flush_pending = False
            gen = _generation
            batch = sorted(_unsaved.items(), key=lambda kv: kv[0][2])
            for bucket, amount in batch:
                _inflight[bucket] = _inflight.get(bucket, Decimal("0")) + amount
            _unsaved.clear()
        for bucket, amount in batch:
            repo, may_alert, spend_date = bucket
            try:
                new_total = repo.add_daily_spend(spend_date, FOUNDRY_PROVIDER, amount)
            except Exception as exc:
                with _lock:
                    if gen == _generation:  # a reset drops the retry with the rest
                        _unsaved[bucket] = _unsaved.get(bucket, Decimal("0")) + amount
                        _settle_inflight(bucket, amount)
                    _last_write_fail_log_at = _rate_limited_warning(
                        _last_write_fail_log_at, kind="write", exc=exc,
                    )
                continue
            with _lock:
                if gen != _generation:
                    continue
                _store_db_total(spend_date, new_total)
                _settle_inflight(bucket, amount)
            if may_alert:
                _check_alert(spend_date, new_total)
    except Exception as exc:  # pragma: no cover - defensive
        _last_write_fail_log_at = _rate_limited_warning(
            _last_write_fail_log_at, kind="flush", exc=exc,
        )


def _check_alert(spend_date: date, new_total: Decimal) -> None:
    """Warn when a write's DB total is at/above the threshold — at most once per
    process per UTC day. ``new_total >= threshold`` (not "this add crossed it")
    so a crossing can never be missed when concurrent workers' adds interleave."""
    try:
        threshold = _settings().alert_usd
        if threshold is None or new_total < threshold:
            return
        with _lock:
            if spend_date in _alerted_dates:
                return
            _alerted_dates.add(spend_date)
        logger.warning(
            "%s: Foundry spend %s USD on %s is at or above the %s USD daily alert threshold",
            ALERT_EVENT_NAME, new_total, spend_date.isoformat(), threshold,
            extra={
                "event_name": ALERT_EVENT_NAME,
                "spend_date": spend_date.isoformat(),
                "total_usd": str(new_total),
                "threshold_usd": str(threshold),
            },
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("foundry spend alert check failed: %s", type(exc).__name__)


def _effective_total_locked(repo: Any, spend_date: date) -> Decimal:
    """Cached DB total + this repo's unsaved + in-flight spend for the day."""
    with _lock:
        base = Decimal("0")
        if _spend_cache is not None and _spend_cache.spend_date == spend_date:
            base = _spend_cache.total_usd
        for buf in (_unsaved, _inflight):
            for (b_repo, _alert, b_date), amount in buf.items():
                if b_repo is repo and b_date == spend_date:
                    base += amount
        return base


def _read_today_total(spend_date: date, ttl_seconds: float) -> Optional[Decimal]:
    """Cached DB total + unsaved + in-flight; None on read failure (fail open)."""
    global _last_read_fail_log_at
    repo = claude_budget.get_spend_repo()
    with _lock:
        cache = _spend_cache
    if (
        cache is not None
        and cache.spend_date == spend_date
        and (time.monotonic() - cache.fetched_at) <= ttl_seconds
    ):
        return _effective_total_locked(repo, spend_date)
    try:
        total = repo.read_daily_spend(spend_date, FOUNDRY_PROVIDER)
    except Exception as exc:
        _last_read_fail_log_at = _rate_limited_warning(
            _last_read_fail_log_at, kind="read", exc=exc,
        )
        return None
    with _lock:
        _store_db_total(spend_date, total)
    return _effective_total_locked(repo, spend_date)


async def pre_call_gate() -> Optional[str]:
    """``"daily_cap"`` when an opt-in Foundry cap is reached, else None.

    No cap configured, isolated harness mode, or a spend read error -> None."""
    cfg = _settings()
    if cfg.cap_usd is None or _is_isolated():
        return None
    total = await asyncio.to_thread(_read_today_total, _utc_today(), cfg.cache_ttl_seconds)
    if total is None:
        return None
    return "daily_cap" if total >= cfg.cap_usd else None
