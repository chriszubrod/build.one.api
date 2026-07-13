# Python Standard Library Imports
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Reason codes that escalate a flag to high priority (mirrors the agent prompt
# + the MCP flag tool). Everything else is medium.
_HIGH_REASONS = frozenset({"over_12_hours", "future_dated", "no_time_logs"})

# Named lock so any future contender (manual re-run, MCP trigger, second timer)
# serializes against the daily sweep — a byte-identical string is the whole
# point of a named lock, so it lives in one place. (Mirrors the
# DRAIN_LOCK_NAME convention on the outbox workers.)
AUTOSUBMIT_LOCK_NAME = "time_autosubmit_sweep"

# The six per-run counters, all zero. Spread into the early-return shapes
# (disabled / skipped) and copied for the mutable running total so a new
# counter is added in exactly one place.
_ZERO_COUNTERS = {
    "submitted": 0, "flagged": 0, "skipped_test": 0,
    "excluded_dup": 0, "submitted_unaggregated": 0, "errors": 0,
}


class TimeEntryAutoSubmitService:
    """
    Deterministic prior-day TimeEntry auto-submit sweep (NO LLM).

    For one work_date (default: yesterday in business_timezone), this is the
    same evaluate -> submit-clean / flag-rest pipeline the time-tracking agent
    runs, but as plain code so it can run unattended on a scheduler:

      1. Pull that day's draft entries (system context, all workers).
      2. Skip test/agent accounts (IsAgent / persona_* / Apple Reviewer).
      3. Dup-safety (prevents double-billing):
         - if a worker-day already has a NON-draft entry, skip all its drafts
           (they duplicate an already-submitted day);
         - if a worker-day has multiple CLEAN drafts, submit only the richest
           one (max hours, then logs), skip the rest.
      4. validate_completeness: is_complete -> submit (draft->submitted +
         billing aggregation); else -> flag (ReviewPriority + ReviewReasons),
         left in draft for manual intervention.

    Mode (Settings.time_autosubmit_mode): off (no-op) | dry_run (report, no
    writes) | on (execute). Submits/flags are attributed to the
    time_tracking_agent user. Each entry is isolated in try/except so one bad
    row can't sink the sweep; the sweep never raises back to the endpoint.
    Idempotent by construction — a re-run only sees entries still in draft.
    """

    # ─── entry points ───────────────────────────────────────────────────────

    def run_for_prior_day(self) -> dict:
        return self.run_for_work_date(self._yesterday_business_date())

    def run_for_stale_drafts(self) -> dict:
        """Sweep EVERY prior-day draft, not just yesterday's.

        The old run_for_prior_day only processes yesterday-in-business-tz.
        If a worker enters a draft after the sweep fires (offline sync,
        next-day catch-up, manual entry days later), the entry sits in
        draft forever — the daily timer never revisits that date. This
        entry point closes that hole: any draft with WorkDate < today
        (business tz) gets evaluated on every run.

        Reuses run_for_work_date() per unique stale date so all
        per-worker-day guarantees (dup-safety, richest-clean-wins,
        actor context, mode gating) hold unchanged. First run in prod
        will submit the built-up backlog of missed days in one pass.

        Mode gating (off / dry_run / on) still applies via
        Settings.time_autosubmit_mode; dry_run returns the report
        without writing.
        """
        from config import Settings
        mode = (Settings().time_autosubmit_mode or "off").strip().lower()
        if mode not in ("dry_run", "on"):
            logger.info("time_autosubmit_stale.disabled mode=%s", mode)
            return {"status": "disabled", "mode": mode, "dates_swept": [],
                    **_ZERO_COUNTERS, "per_day": []}

        business_today = datetime.now(self._business_tz()).date()
        dates = self._read_stale_draft_dates(business_today)

        totals = dict(_ZERO_COUNTERS)
        per_day = []
        all_actions: list[dict] = []

        for d in dates:
            iso = d.isoformat() if hasattr(d, "isoformat") else str(d)
            result = self.run_for_work_date(iso)
            per_day.append({
                "work_date": iso,
                "submitted": result.get("submitted", 0),
                "flagged": result.get("flagged", 0),
                "skipped_test": result.get("skipped_test", 0),
                "excluded_dup": result.get("excluded_dup", 0),
                "submitted_unaggregated": result.get("submitted_unaggregated", 0),
                "errors": result.get("errors", 0),
            })
            for k in totals:
                totals[k] += result.get(k, 0)
            all_actions.extend(result.get("actions", []))

        summary = {
            "status": "ok",
            "mode": mode,
            "dates_swept": [d.isoformat() if hasattr(d, "isoformat") else str(d)
                            for d in dates],
            **totals,
            "per_day": per_day,
            "actions": all_actions[:200],
            "actions_truncated": len(all_actions) > 200,
        }
        logger.info("time_autosubmit_stale.sweep_complete %s",
                    {k: v for k, v in summary.items() if k not in ("actions", "per_day")})
        return summary

    def run_for_work_date(self, work_date: str) -> dict:
        from config import Settings

        settings = Settings()
        mode = (settings.time_autosubmit_mode or "off").strip().lower()
        if mode not in ("dry_run", "on"):
            logger.info("time_autosubmit.disabled mode=%s work_date=%s", mode, work_date)
            return {"status": "disabled", "mode": mode, "work_date": work_date,
                    **_ZERO_COUNTERS}

        # Serialize the sweep across processes so a manual ?work_date= re-run
        # overlapping the daily timer (or two overlapping runs) can't double-
        # write submitted rows / double-enqueue review notifications.
        # timeout_ms=0 => skip immediately if another sweep holds the lock;
        # never block/wait. The 'off/disabled' no-op above stays lock-free.
        from shared.db_lock import app_lock
        with app_lock(AUTOSUBMIT_LOCK_NAME, timeout_ms=0) as acquired:
            if not acquired:
                logger.info("time_autosubmit.skipped_locked work_date=%s", work_date)
                return {"status": "skipped", "reason": "already_running",
                        "mode": mode, "work_date": work_date, **_ZERO_COUNTERS}
            return self._sweep_work_date(work_date, mode)

    def _sweep_work_date(self, work_date: str, mode: str) -> dict:
        actor_id = self._resolve_actor_id()
        from shared.authz.context import set_authz_context

        set_authz_context(user_id=actor_id, company_id=None, is_system_admin=True)

        # Evaluate the deterministic checklist against the business-timezone
        # "today" (not the container's UTC date) so future_dated is consistent
        # with how the sweep picks work_date.
        business_today = datetime.now(self._business_tz()).date()

        day_rows = self._read_day_entries(work_date)
        uinfo = self._read_user_info({r["user_id"] for r in day_rows if r["user_id"]})

        # Group every entry (any status) for the day by worker, for dup-safety.
        by_worker: dict = {}
        for r in day_rows:
            by_worker.setdefault(r["user_id"], []).append(r)

        submitted = flagged = skipped_test = excluded_dup = errors = unaggregated = 0
        actions: list[dict] = []

        for user_id, entries in by_worker.items():
            name, is_agent, username = uinfo.get(user_id, ("?", False, ""))
            if self._is_test(name, is_agent, username):
                skipped_test += sum(1 for e in entries if e["status"] == "draft")
                continue

            drafts = [e for e in entries if e["status"] == "draft"]
            if not drafts:
                continue

            # Rule 1: a non-draft sibling means the day is already submitted —
            # every draft here is a duplicate; do not re-submit.
            if any(e["status"] != "draft" for e in entries):
                excluded_dup += len(drafts)
                for e in drafts:
                    actions.append({"date": work_date, "worker": name,
                                    "public_id": e["public_id"], "action": "excluded_dup"})
                continue

            # Evaluate each draft once.
            evaluated = []
            for e in drafts:
                try:
                    rep, hours, logs = self._evaluate(e["public_id"], business_today)
                    evaluated.append((e, rep, hours, logs))
                except Exception as err:
                    errors += 1
                    logger.exception("time_autosubmit.evaluate_failed public_id=%s: %s",
                                     e["public_id"], err)

            clean = [t for t in evaluated if t[1]["is_complete"]]
            dirty = [t for t in evaluated if not t[1]["is_complete"]]

            # Rule 2: multiple clean drafts on one worker-day -> submit only the
            # richest (most hours, then most logs, then lowest id); skip rest.
            submit_targets = clean
            if len(clean) > 1:
                clean_sorted = sorted(
                    clean, key=lambda t: (t[2], t[3], -t[0]["id"]), reverse=True
                )
                submit_targets = clean_sorted[:1]
                for e, _rep, _h, _l in clean_sorted[1:]:
                    excluded_dup += 1
                    actions.append({"date": work_date, "worker": name,
                                    "public_id": e["public_id"], "action": "excluded_dup_multidraft"})

            for e, rep, _hours, _logs in submit_targets:
                try:
                    if mode == "on":
                        self._submit(e["public_id"], actor_id)
                        if not self._has_labor_row(e["id"]):
                            unaggregated += 1
                            logger.warning("time_autosubmit.submitted_unaggregated "
                                           "public_id=%s worker=%s", e["public_id"], name)
                    submitted += 1
                    actions.append({"date": work_date, "worker": name,
                                    "public_id": e["public_id"], "action": "submit"})
                except Exception as err:
                    errors += 1
                    logger.exception("time_autosubmit.submit_failed public_id=%s: %s",
                                     e["public_id"], err)

            for e, rep, _hours, _logs in dirty:
                reasons = rep["reasons"]
                prio = "high" if (set(reasons) & _HIGH_REASONS or len(reasons) >= 3) else "medium"
                try:
                    if mode == "on":
                        self._flag(e["public_id"], prio, reasons)
                    flagged += 1
                    actions.append({"date": work_date, "worker": name, "public_id": e["public_id"],
                                    "action": "flag", "priority": prio, "reasons": reasons})
                except Exception as err:
                    errors += 1
                    logger.exception("time_autosubmit.flag_failed public_id=%s: %s",
                                     e["public_id"], err)

        summary = {
            "status": "ok",
            "mode": mode,
            "work_date": work_date,
            "submitted": submitted,
            "flagged": flagged,
            "skipped_test": skipped_test,
            "excluded_dup": excluded_dup,
            "submitted_unaggregated": unaggregated,
            "errors": errors,
            "actions": actions[:200],
            "actions_truncated": len(actions) > 200,
        }
        logger.info("time_autosubmit.sweep_complete %s",
                    {k: v for k, v in summary.items() if k != "actions"})
        return summary

    # ─── evaluation + mutations (service layer) ─────────────────────────────

    @staticmethod
    def _evaluate(public_id: str, today):
        """Return (report, work_hours Decimal, log_count) for a draft entry."""
        from entities.time_entry.business.validation import validate_completeness
        from entities.time_entry.persistence.repo import TimeEntryRepository
        from entities.time_entry.persistence.time_log_repo import TimeLogRepository

        entry = TimeEntryRepository().read_by_public_id(
            public_id=public_id, actor_is_system_admin=True)
        if not entry:
            raise ValueError(f"TimeEntry {public_id} not found")
        logs = TimeLogRepository().read_by_time_entry_id(
            time_entry_id=entry.id, actor_is_system_admin=True)
        rep = validate_completeness(entry=entry, logs=logs, today=today)
        hours = Decimal(str(rep["summary"]["total_work_hours"]))
        return rep, hours, len(logs)

    @staticmethod
    def _submit(public_id: str, actor_id: int) -> None:
        from entities.time_entry.business.service import TimeEntryService
        TimeEntryService().submit(public_id=public_id, user_id=actor_id)

    @staticmethod
    def _flag(public_id: str, priority: str, reasons: list) -> None:
        from entities.time_entry.business.service import TimeEntryService
        TimeEntryService().stamp_review(public_id=public_id, priority=priority, reasons=reasons)

    @staticmethod
    def _has_labor_row(time_entry_id: int) -> bool:
        from shared.database import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM dbo.ContractLabor WHERE SourceTimeEntryId=? "
                "UNION ALL SELECT 1 FROM dbo.EmployeeLabor WHERE SourceTimeEntryId=?",
                (time_entry_id, time_entry_id),
            )
            return cur.fetchone() is not None

    # ─── data access ────────────────────────────────────────────────────────

    @staticmethod
    def _read_day_entries(work_date: str) -> list[dict]:
        from shared.database import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT te.Id, CONVERT(varchar(36), te.PublicId) AS PublicId, te.UserId,
                       (SELECT TOP 1 s.Status FROM dbo.TimeEntryStatus s
                        WHERE s.TimeEntryId = te.Id
                        ORDER BY s.CreatedDatetime DESC, s.Id DESC) AS Status
                FROM dbo.TimeEntry te
                WHERE te.WorkDate = ?
                """,
                (work_date,),
            )
            rows = []
            for r in cur.fetchall():
                if r[3] is None:
                    # Structurally broken row (no status history) — never submit
                    # blindly; quarantine + log rather than treat as 'draft'.
                    logger.warning("time_autosubmit.no_status_history public_id=%s", r[1])
                    continue
                rows.append({
                    "id": int(r[0]),
                    "public_id": r[1],
                    "user_id": (int(r[2]) if r[2] is not None else None),
                    "status": r[3],
                })
            return rows

    @staticmethod
    def _read_stale_draft_dates(business_today) -> list:
        """Distinct WorkDates < business_today that still have at least one
        entry whose current (latest) status is 'draft'. Ordered oldest→newest
        so the sweep processes the longest-stalled days first — clearer log
        output + safer if we later add a per-run time cap.

        Returns dates as SQL-server date objects; caller isoformats them.
        """
        from shared.database import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT DISTINCT te.WorkDate
                FROM dbo.TimeEntry te
                OUTER APPLY (
                    SELECT TOP 1 s.Status
                    FROM dbo.TimeEntryStatus s
                    WHERE s.TimeEntryId = te.Id
                    ORDER BY s.CreatedDatetime DESC, s.Id DESC
                ) cs
                WHERE cs.Status = 'draft'
                  AND te.WorkDate < ?
                ORDER BY te.WorkDate ASC
                """,
                (business_today,),
            )
            return [r[0] for r in cur.fetchall()]

    @staticmethod
    def _read_user_info(user_ids: set) -> dict:
        ids = [u for u in user_ids if u is not None]
        if not ids:
            return {}
        from shared.database import get_connection
        placeholders = ",".join("?" for _ in ids)
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT u.Id, LTRIM(RTRIM(CONCAT(u.Firstname,' ',u.Lastname))), "
                f"u.IsAgent, a.Username FROM dbo.[User] u "
                f"LEFT JOIN dbo.Auth a ON a.UserId=u.Id WHERE u.Id IN ({placeholders})",
                *ids,
            )
            return {int(r[0]): (r[1], bool(r[2]) if r[2] is not None else False, (r[3] or ""))
                    for r in cur.fetchall()}

    @staticmethod
    def _resolve_actor_id() -> int:
        """The user recorded as submitter/flagger. Prefer the time_tracking_agent;
        fall back to the system default (17 = Christopher)."""
        from shared.database import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT u.Id FROM dbo.[User] u JOIN dbo.Auth a ON a.UserId=u.Id "
                        "WHERE a.Username='time_tracking_agent'")
            row = cur.fetchone()
        return int(row[0]) if row else 17

    # ─── helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _is_test(name: str, is_agent: bool, username: str) -> bool:
        return bool(is_agent) or (username or "").startswith("persona_") or name == "Apple Reviewer"

    @staticmethod
    def _business_tz() -> ZoneInfo:
        from config import Settings
        name = (getattr(Settings(), "business_timezone", None) or "America/Chicago").strip()
        try:
            return ZoneInfo(name)
        except Exception:
            logger.warning("time_autosubmit.bad_timezone name=%s -> America/Chicago", name)
            return ZoneInfo("America/Chicago")

    def _yesterday_business_date(self) -> str:
        return (datetime.now(self._business_tz()) - timedelta(days=1)).date().isoformat()
