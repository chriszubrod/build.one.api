# Python Standard Library Imports
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional

# Local Imports
from integrations.intuit.qbo.base.errors import QboBudgetExceededError

logger = logging.getLogger(__name__)


# Intuit Builder (free) tier: 500,000 CorePlus calls per calendar month,
# hard-capped — excess calls are BLOCKED, not billed (July 2026 incident:
# a reconcile bug consumed 94% of the cap and froze all QBO sync for days).
DEFAULT_MONTHLY_CALL_BUDGET = 500_000
DEFAULT_BLOCK_FRACTION = 0.95
DEFAULT_WARN_FRACTION = 0.80


def monthly_call_budget() -> int:
    """Monthly call ceiling (`QBO_MONTHLY_CALL_BUDGET`, default 500,000)."""
    raw = os.getenv("QBO_MONTHLY_CALL_BUDGET", "").strip()
    if not raw:
        return DEFAULT_MONTHLY_CALL_BUDGET
    try:
        value = int(raw)
        return value if value > 0 else DEFAULT_MONTHLY_CALL_BUDGET
    except ValueError:
        return DEFAULT_MONTHLY_CALL_BUDGET


def _fraction_env(name: str, default: float) -> float:
    """Parse a 0-1 fraction env var; malformed or out-of-range → default."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
        return value if 0.0 < value <= 1.0 else default
    except ValueError:
        return default


def block_fraction() -> float:
    """Breaker threshold as a fraction of budget (`QBO_BUDGET_BLOCK_PCT`, default 0.95)."""
    return _fraction_env("QBO_BUDGET_BLOCK_PCT", DEFAULT_BLOCK_FRACTION)


def warn_fraction() -> float:
    """Warn threshold as a fraction of budget (`QBO_BUDGET_WARN_PCT`, default 0.80)."""
    return _fraction_env("QBO_BUDGET_WARN_PCT", DEFAULT_WARN_FRACTION)


def enforcement_enabled() -> bool:
    """
    Kill switch for the breaker (`QBO_BUDGET_ENFORCE`). Default ON: only an
    explicit "false" disables blocking. Metering always runs regardless.
    """
    return os.getenv("QBO_BUDGET_ENFORCE", "").strip().lower() != "false"


def current_month_key(now: Optional[datetime] = None) -> str:
    """UTC 'YYYY-MM' — Intuit resets the CorePlus cap on the 1st of the month."""
    moment = now or datetime.now(timezone.utc)
    return moment.strftime("%Y-%m")


def next_month_start(now: Optional[datetime] = None) -> datetime:
    """First instant (UTC) of the month after `now` — when the cap resets."""
    moment = now or datetime.now(timezone.utc)
    if moment.month == 12:
        return datetime(moment.year + 1, 1, 1, tzinfo=timezone.utc)
    return datetime(moment.year, moment.month + 1, 1, tzinfo=timezone.utc)


def reset_at_for_month(month_key: Optional[str]) -> datetime:
    """
    First instant (UTC) of the month after `month_key` ('YYYY-MM') — the
    reset for the month whose budget was actually exhausted. Falls back to
    wall-clock `next_month_start()` when the key is absent or malformed.
    Used by the outbox park path: computing from the error's month key (not
    handler wall clock) keeps a row raised in month M but handled after the
    UTC rollover to M+1 parked until the 1st of M+1, not M+2.
    """
    if month_key:
        try:
            anchor = datetime.strptime(month_key, "%Y-%m").replace(tzinfo=timezone.utc)
            return next_month_start(anchor)
        except ValueError:
            pass
    return next_month_start()


@dataclass(frozen=True)
class BudgetStatus:
    """Snapshot of the month's QBO API usage vs the breaker thresholds."""

    month_key: str
    call_count: int
    budget: int
    block_threshold: int
    warn_threshold: int
    enforced: bool
    #: True when the meter could not be read/written this call (DB error).
    #: The meter fails OPEN: a broken meter must never take down QBO sync.
    meter_unavailable: bool = False

    @property
    def blocked(self) -> bool:
        """Enforcement on AND count at/over the block threshold → refuse the call."""
        return (
            self.enforced
            and not self.meter_unavailable
            and self.call_count >= self.block_threshold
        )

    @property
    def warning(self) -> bool:
        """Count at/over the warn threshold (fires regardless of enforcement)."""
        return not self.meter_unavailable and self.call_count >= self.warn_threshold


def _build_status(
    month_key: str, call_count: int, *, meter_unavailable: bool = False
) -> BudgetStatus:
    budget = monthly_call_budget()
    return BudgetStatus(
        month_key=month_key,
        call_count=call_count,
        budget=budget,
        block_threshold=int(budget * block_fraction()),
        warn_threshold=int(budget * warn_fraction()),
        enforced=enforcement_enabled(),
        meter_unavailable=meter_unavailable,
    )


class QboApiUsageRepository:
    """Persistence for `[qbo].[ApiUsage]` via sprocs in `base/sql/qbo.api_usage.sql`."""

    def increment(self, realm_id: str, month_key: str) -> int:
        """Atomically add one to the single (RealmId, MonthKey) counter; return its new total.

        See ``QboApiBudget.record_call`` for how this per-realm count is compared
        against ``status()``'s cross-realm sum.
        """
        # Lazy import: keep budget.py importable in the pure-logic test
        # harness without a DB driver present.
        from shared.database import call_procedure, get_connection, map_database_error

        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="IncrementQboApiUsage",
                        params={
                            "RealmId": realm_id,
                            "MonthKey": month_key,
                        },
                    )
                    row = cursor.fetchone()
                    return int(row[0]) if row else 0
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            raise map_database_error(error)

    def read_month_total(self, month_key: str) -> int:
        """Sum of the month's counters across realms (single-realm today)."""
        from shared.database import call_procedure, get_connection, map_database_error

        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboApiUsageByMonth",
                        params={"MonthKey": month_key},
                    )
                    rows = cursor.fetchall()
                    return sum(int(r.CallCount) for r in rows)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            raise map_database_error(error)


class QboApiBudget:
    """
    Durable per-call meter + monthly-budget breaker for the QBO API.

    `record_call` is invoked at the single HTTP choke points (QboHttpClient
    ._send_http and QboAttachableClient) once per real HTTP round-trip:
    increment first, then compare the returned month-to-date count against
    the block threshold. Increment-before-check means a blocked attempt
    still counts — deliberate: it is conservative in the safe direction and
    only occurs while already at the ceiling.

    The meter FAILS OPEN: any DB error while metering logs loudly and lets
    the call proceed. A broken meter must never become the thing that takes
    down QBO sync.
    """

    def __init__(self, repo: Optional[QboApiUsageRepository] = None):
        self.repo = repo or QboApiUsageRepository()
        # Last warn/block band logged, per month key — so threshold
        # crossings log once per process instead of once per call.
        self._logged_band: Dict[str, str] = {}

    def record_call(self, realm_id: str) -> BudgetStatus:
        """Meter one QBO API call and return the resulting budget status.

        Compares the per-realm ``increment`` count for ``realm_id`` against the
        block threshold. ``status()`` reports the cross-realm ``read_month_total``
        sum instead — identical while realm count == 1; with multiple realms each
        realm's breaker can under-block while ``status()`` already shows the
        combined total over the cap. Known, deliberately deferred — see TODO.md
        "Per-realm vs cross-realm breaker inconsistency".
        """
        month_key = current_month_key()
        try:
            count = self.repo.increment(realm_id, month_key)
        except Exception as error:
            return self._fail_open(month_key, realm_id, error)
        status = _build_status(month_key, count)
        self._log_band_crossing(status)
        return status

    def record_call_or_raise(self, realm_id: str, *, method: str, path: str) -> BudgetStatus:
        """
        Meter one call and refuse it (QboBudgetExceededError) when blocked.
        The single home for the refusal message/payload — both HTTP clients
        call this instead of hand-building the error.
        """
        status = self.record_call(realm_id)
        if status.blocked:
            raise QboBudgetExceededError(
                f"QBO call refused: month-to-date API usage {status.call_count} has crossed "
                f"the block threshold {status.block_threshold} (budget {status.budget}, "
                f"month {status.month_key})",
                month_key=status.month_key,
                call_count=status.call_count,
                budget=status.budget,
                request_method=method,
                request_path=path,
            )
        return status

    def status(self) -> BudgetStatus:
        """Read-only status (no increment) — for up-front drain/pull checks."""
        month_key = current_month_key()
        try:
            count = self.repo.read_month_total(month_key)
        except Exception as error:
            return self._fail_open(month_key, None, error)
        return _build_status(month_key, count)

    def _fail_open(
        self, month_key: str, realm_id: Optional[str], error: Exception
    ) -> BudgetStatus:
        logger.error(
            "qbo.budget.meter_unavailable",
            extra={
                "event_name": "qbo.budget.meter_unavailable",
                "realm_id": realm_id,
                "month_key": month_key,
                "error": str(error),
            },
        )
        return _build_status(month_key, 0, meter_unavailable=True)

    def _log_band_crossing(self, status: BudgetStatus) -> None:
        band = "ok"
        if status.call_count >= status.block_threshold:
            band = "blocked"
        elif status.warning:
            band = "warn"
        if self._logged_band.get(status.month_key) == band:
            return
        self._logged_band[status.month_key] = band
        if band == "ok":
            return
        log = logger.error if band == "blocked" else logger.warning
        log(
            "qbo.budget.threshold_crossed",
            extra={
                "event_name": "qbo.budget.threshold_crossed",
                "band": band,
                "month_key": status.month_key,
                "call_count": status.call_count,
                "budget": status.budget,
                "block_threshold": status.block_threshold,
                "warn_threshold": status.warn_threshold,
                "enforced": status.enforced,
            },
        )


_shared_budget: Optional[QboApiBudget] = None


def get_qbo_api_budget() -> QboApiBudget:
    """Process-wide shared meter (keeps the band-crossing log dedup in one place)."""
    global _shared_budget
    if _shared_budget is None:
        _shared_budget = QboApiBudget()
    return _shared_budget
