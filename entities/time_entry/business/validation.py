# Python Standard Library Imports
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

# Local Imports
from entities.time_entry.business.model import TimeEntry, TimeLog


# ─── Reason code vocabulary ──────────────────────────────────────────────
# Stable string codes the time_tracking_specialist agent maps to
# ReviewPriority. Kept short for ReviewReasons JSON storage.
REASON_NO_TIME_LOGS = "no_time_logs"
REASON_NULL_PROJECT = "null_project"
REASON_MISSING_CLOCKOUT = "missing_clockout"
REASON_OVERNIGHT_SHIFT = "overnight_shift"
REASON_OVER_12_HOURS = "over_12_hours"
REASON_UNDER_15_MINUTES = "under_15_minutes"
REASON_FUTURE_DATED = "future_dated"
REASON_GPS_NO_PROJECT = "gps_no_project"

ALL_REASON_CODES = (
    REASON_NO_TIME_LOGS,
    REASON_NULL_PROJECT,
    REASON_MISSING_CLOCKOUT,
    REASON_OVERNIGHT_SHIFT,
    REASON_OVER_12_HOURS,
    REASON_UNDER_15_MINUTES,
    REASON_FUTURE_DATED,
    REASON_GPS_NO_PROJECT,
)


# ─── Helpers ─────────────────────────────────────────────────────────────

def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    """Best-effort ISO-8601 parse. Returns None on failure or empty input."""
    if not value:
        return None
    try:
        # `fromisoformat` handles 'YYYY-MM-DD HH:MM:SS' + 'YYYY-MM-DDTHH:MM:SS[.fffff][+HH:MM]'.
        # Trim trailing 'Z' if present.
        s = value.rstrip("Z") if value.endswith("Z") else value
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _is_overnight(log: TimeLog) -> bool:
    """True iff clock_in and clock_out are on different calendar dates."""
    ci = _parse_dt(log.clock_in)
    co = _parse_dt(log.clock_out)
    if not ci or not co:
        return False
    return ci.date() != co.date()


def _as_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


# ─── Public API ──────────────────────────────────────────────────────────

def validate_completeness(
    entry: TimeEntry,
    logs: List[TimeLog],
    *,
    today: Optional[date] = None,
) -> dict:
    """
    Run the deterministic anomaly checklist against a TimeEntry + its
    TimeLogs. Pure function — no DB access, no mutation.

    Returns a structured report:

        {
          "time_entry_public_id": "<uuid>",
          "is_complete": bool,            # True iff reasons is empty
          "reasons": ["null_project", ...], # short codes from ALL_REASON_CODES
          "summary": {
            "work_date": "YYYY-MM-DD",
            "log_count": N,                # all logs (work + break)
            "work_log_count": N,           # logs with log_type='work' (or None)
            "total_work_hours": "8.5",     # str(Decimal) — sum of work-log Duration
          }
        }

    Hours math: only `log_type='work'` (or NULL log_type — treated as work for
    legacy rows) contribute to total_work_hours. Break rows are ignored.

    `today` defaults to today's local date; injectable for tests.
    """
    today = today or date.today()
    reasons: List[str] = []

    if not logs:
        reasons.append(REASON_NO_TIME_LOGS)

    # Per-log checks — one pass.
    has_null_project = False
    has_missing_clockout = False
    has_overnight = False
    has_gps_no_project = False

    work_logs: List[TimeLog] = []
    for log in logs:
        lt = (log.log_type or "work").lower()
        if lt == "work":
            work_logs.append(log)

        if log.project_id is None:
            has_null_project = True
            if log.latitude is not None or log.longitude is not None:
                has_gps_no_project = True

        if log.clock_out is None:
            has_missing_clockout = True

        if _is_overnight(log):
            has_overnight = True

    if has_null_project:
        reasons.append(REASON_NULL_PROJECT)
    if has_missing_clockout:
        reasons.append(REASON_MISSING_CLOCKOUT)
    if has_overnight:
        reasons.append(REASON_OVERNIGHT_SHIFT)
    if has_gps_no_project:
        reasons.append(REASON_GPS_NO_PROJECT)

    # Total work hours.
    total_work_hours = sum((_as_decimal(l.duration) for l in work_logs), Decimal("0"))

    if total_work_hours > Decimal("12"):
        reasons.append(REASON_OVER_12_HOURS)
    # Under 15 min only applies if there's *some* recorded time — zero hours
    # is already caught by REASON_NO_TIME_LOGS / REASON_MISSING_CLOCKOUT.
    if Decimal("0") < total_work_hours < Decimal("0.25"):
        reasons.append(REASON_UNDER_15_MINUTES)

    # Future-dated: compare ISO YYYY-MM-DD strings (entry.work_date is stored
    # as 'YYYY-MM-DD').
    if entry.work_date and entry.work_date > today.isoformat():
        reasons.append(REASON_FUTURE_DATED)

    return {
        "time_entry_public_id": entry.public_id,
        "is_complete": not reasons,
        "reasons": reasons,
        "summary": {
            "work_date": entry.work_date,
            "log_count": len(logs),
            "work_log_count": len(work_logs),
            "total_work_hours": str(total_work_hours),
        },
    }
