# Python Standard Library Imports
import html
import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


# Fixed namespace for deriving a DETERMINISTIC outbox EntityPublicId per
# (worker, work_date). uuid5 means a re-run of the daily sweep produces the
# same GUID, so the CountMsOutboxByEntity idempotency check catches an
# already-enqueued digest (any status) and skips it. Do not change — changing
# it would make every past digest look un-sent.
_DIGEST_NAMESPACE = uuid.UUID("b1d9e7a4-3c2f-4e8a-9b6d-7f0a1c2e3d40")

_ENTITY_TYPE = "TimeEntryDigest"


class TimeEntryDigestService:
    """
    Builds + enqueues the per-worker morning "time recorded for you" email.

    One email per worker who had any TimeEntry on the target work_date
    (default: yesterday in the business timezone). The body summarizes each
    entry's logs (project, clock in/out, hours), total hours, the worker's
    note, and the entry's current status, so the worker can confirm
    correctness and flag errors to their manager.

    Delivery rides the existing MS outbox (`send_mail` Kind) exactly like the
    review notifications — never an inline Graph call. Mode is governed by
    `Settings.time_entry_digest_mode` (off | draft | send); 'off' makes the
    whole sweep a no-op (the kill switch). 'draft' deposits a draft per worker
    in the sender mailbox's Drafts folder for a human to review + send; 'send'
    dispatches directly.

    Failure semantics: each worker is enqueued inside its own try/except so a
    single bad worker can't sink the batch, and the whole sweep never raises
    back to the admin endpoint. Workers with no resolvable Contact email are
    logged and skipped. Idempotent per (worker, work_date) via a deterministic
    outbox EntityPublicId.
    """

    # ─── entry points ───────────────────────────────────────────────────────

    def run_for_yesterday(self) -> dict:
        """Run the sweep for yesterday in the configured business timezone."""
        return self.run_for_work_date(self._yesterday_business_date())

    def run_for_work_date(self, work_date: str) -> dict:
        """
        Run the digest sweep for a single work_date (YYYY-MM-DD). Returns a
        summary dict for the admin-endpoint timing envelope.
        """
        from config import Settings

        settings = Settings()
        mode = (settings.time_entry_digest_mode or "off").strip().lower()
        if mode not in ("draft", "send"):
            logger.info(
                "time_entry_digest.disabled mode=%s work_date=%s", mode, work_date
            )
            return {
                "status": "disabled",
                "mode": mode,
                "work_date": work_date,
                "workers_notified": 0,
            }

        from entities.time_entry.persistence.digest_repo import TimeEntryDigestRepository

        rows = TimeEntryDigestRepository().read_for_work_date(work_date)
        workers = self._group_by_worker(rows)

        notified = 0
        skipped_no_email = 0
        skipped_already_sent = 0
        refused = 0
        failed = 0

        bcc = self._resolve_bcc(settings)

        for worker in workers:
            try:
                outcome = self._enqueue_for_worker(
                    worker=worker,
                    work_date=work_date,
                    mode=mode,
                    bcc=bcc,
                )
                if outcome == "notified":
                    notified += 1
                elif outcome == "no_email":
                    skipped_no_email += 1
                elif outcome == "already_sent":
                    skipped_already_sent += 1
                elif outcome == "refused":
                    refused += 1
            except Exception as error:
                failed += 1
                logger.exception(
                    "time_entry_digest.worker_failed work_date=%s user_id=%s: %s",
                    work_date,
                    worker.get("user_id"),
                    error,
                )

        summary = {
            "status": "ok",
            "mode": mode,
            "work_date": work_date,
            "workers_total": len(workers),
            "workers_notified": notified,
            "skipped_no_email": skipped_no_email,
            "skipped_already_sent": skipped_already_sent,
            "refused_ms_writes_gate": refused,
            "failed": failed,
        }
        logger.info("time_entry_digest.sweep_complete %s", summary)
        return summary

    # ─── per-worker enqueue ─────────────────────────────────────────────────

    def _enqueue_for_worker(self, *, worker: dict, work_date: str, mode: str, bcc: list) -> str:
        from integrations.ms.outbox.business.service import MsOutboxService
        from integrations.ms.outbox.persistence.repo import MsOutboxRepository

        email = worker.get("email")
        if not email:
            logger.warning(
                "time_entry_digest.unreachable work_date=%s user_id=%s "
                "reason=no_contact_email",
                work_date,
                worker.get("user_id"),
            )
            return "no_email"

        # Deterministic per (worker, work_date) → idempotent re-runs.
        entity_public_id = str(
            uuid.uuid5(_DIGEST_NAMESPACE, f"{worker['user_public_id']}:{work_date}")
        )
        if MsOutboxRepository().count_by_entity(_ENTITY_TYPE, entity_public_id) > 0:
            logger.info(
                "time_entry_digest.already_enqueued work_date=%s user_id=%s",
                work_date,
                worker.get("user_id"),
            )
            return "already_sent"

        subject = self._build_subject(worker_name=self._worker_name(worker), work_date=work_date)
        body_html = self._build_html_body(worker=worker, work_date=work_date)

        result = MsOutboxService().enqueue_send_mail(
            entity_type=_ENTITY_TYPE,
            entity_public_id=entity_public_id,
            to_addresses=[{"email": email, "name": self._worker_name(worker)}],
            cc_addresses=[],
            bcc_addresses=bcc,
            subject=subject,
            body=body_html,
            body_type="HTML",
            mode=mode,
        )
        if result is None:
            logger.info(
                "time_entry_digest.enqueue_refused work_date=%s user_id=%s "
                "reason=ms_writes_gate",
                work_date,
                worker.get("user_id"),
            )
            return "refused"

        logger.info(
            "time_entry_digest.enqueued work_date=%s user_id=%s outbox_public_id=%s "
            "mode=%s entries=%d",
            work_date,
            worker.get("user_id"),
            result.public_id,
            mode,
            len(worker.get("entries", [])),
        )
        return "notified"

    # ─── grouping ───────────────────────────────────────────────────────────

    @staticmethod
    def _group_by_worker(rows: list[dict]) -> list[dict]:
        """Flat (entry x log) rows → [{worker, entries: [{..., logs: [...]}]}]."""
        workers: dict = {}
        for r in rows:
            uid = r.get("UserId")
            w = workers.setdefault(
                uid,
                {
                    "user_id": uid,
                    "user_public_id": str(r.get("UserPublicId")),
                    "firstname": (r.get("Firstname") or "").strip(),
                    "lastname": (r.get("Lastname") or "").strip(),
                    "email": r.get("Email"),
                    "_entries": {},
                },
            )
            te_id = r.get("TimeEntryId")
            entry = w["_entries"].setdefault(
                te_id,
                {
                    "time_entry_public_id": str(r.get("TimeEntryPublicId")),
                    "status": r.get("CurrentStatus"),
                    "note": r.get("EntryNote"),
                    "logs": [],
                },
            )
            if r.get("TimeLogId") is not None:
                entry["logs"].append(
                    {
                        "project": r.get("ProjectName") or r.get("ProjectAbbreviation"),
                        "clock_in": r.get("ClockIn"),
                        "clock_out": r.get("ClockOut"),
                        "duration": r.get("Duration"),
                        "log_type": r.get("LogType"),
                        "note": r.get("LogNote"),
                    }
                )

        result = []
        for w in workers.values():
            w["entries"] = list(w.pop("_entries").values())
            result.append(w)
        return result

    # ─── timezone ───────────────────────────────────────────────────────────

    def _yesterday_business_date(self) -> str:
        from config import Settings

        tz = self._business_tz(Settings())
        return (datetime.now(tz) - timedelta(days=1)).date().isoformat()

    @staticmethod
    def _business_tz(settings) -> ZoneInfo:
        name = (getattr(settings, "business_timezone", None) or "America/Chicago").strip()
        try:
            return ZoneInfo(name)
        except Exception:
            logger.warning(
                "time_entry_digest.bad_timezone name=%s — falling back to America/Chicago",
                name,
            )
            return ZoneInfo("America/Chicago")

    @staticmethod
    def _resolve_bcc(settings) -> list:
        addr = (
            getattr(settings, "time_entry_digest_bcc", None)
            or getattr(settings, "invoice_inbox_email", None)
        )
        if addr:
            return [{"email": addr, "name": "Timesheet archive"}]
        return []

    # ─── subject + body ─────────────────────────────────────────────────────

    @classmethod
    def _build_subject(cls, *, worker_name: str, work_date: str) -> str:
        return f"TimeEntry - {worker_name} - {cls._format_date_subject(work_date)}"

    @classmethod
    def _build_html_body(cls, *, worker: dict, work_date: str) -> str:
        firstname = html.escape(worker.get("firstname") or "there")
        date_long = html.escape(cls._format_date_long(work_date))

        total = Decimal("0")
        sections = []
        for entry in worker.get("entries", []):
            rows_html = []
            for log in entry.get("logs", []):
                dur = cls._to_decimal(log.get("duration"))
                if dur is not None:
                    total += dur
                rows_html.append(
                    "<tr>"
                    f"<td>{html.escape(log.get('project') or '(unassigned)')}</td>"
                    f"<td>{html.escape(cls._fmt_clock(log.get('clock_in')))}</td>"
                    f"<td>{html.escape(cls._fmt_clock(log.get('clock_out')))}</td>"
                    f"<td style='text-align:right;'>{cls._fmt_hours(dur)}</td>"
                    f"<td>{html.escape((log.get('log_type') or '').title())}</td>"
                    f"<td>{html.escape(log.get('note') or '')}</td>"
                    "</tr>"
                )

            if rows_html:
                table = (
                    "<table cellpadding='4' cellspacing='0' border='1' "
                    "style='border-collapse:collapse; margin-top:6px;'>"
                    "<tr>"
                    "<th align='left'>Project</th>"
                    "<th align='left'>Clock In</th>"
                    "<th align='left'>Clock Out</th>"
                    "<th align='right'>Hours</th>"
                    "<th align='left'>Type</th>"
                    "<th align='left'>Note</th>"
                    "</tr>"
                    f"{''.join(rows_html)}</table>"
                )
            else:
                table = "<p style='margin-top:6px;'><em>No clock entries recorded.</em></p>"

            note = entry.get("note")
            note_html = (
                f"<p style='margin:4px 0;'>Note: {html.escape(note)}</p>" if note else ""
            )
            sections.append(f"{table}{note_html}")

        total_html = f"<p style='margin-top:8px;'>Total hours: <strong>{cls._fmt_hours(total)}</strong></p>"

        return (
            f"<p>{firstname},</p>"
            f"<p>Here's a summary of the time recorded for you on {date_long}.</p>"
            f"{''.join(sections)}"
            f"{total_html}"
            "<p style='margin-top:8px;'>If everything looks correct, no action is needed.</p>"
        )

    # ─── formatting helpers ─────────────────────────────────────────────────

    @staticmethod
    def _worker_name(worker: dict) -> str:
        name = f"{worker.get('firstname') or ''} {worker.get('lastname') or ''}".strip()
        return name or "Team member"

    @staticmethod
    def _to_decimal(value) -> Optional[Decimal]:
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except Exception:
            return None

    @classmethod
    def _fmt_hours(cls, value) -> str:
        dec = value if isinstance(value, Decimal) else cls._to_decimal(value)
        if dec is None:
            return "—"
        return f"{dec:.2f}"

    @staticmethod
    def _parse_dt(raw) -> Optional[datetime]:
        if raw is None:
            return None
        if isinstance(raw, datetime):
            return raw
        s = str(raw)
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    @classmethod
    def _fmt_clock(cls, raw) -> str:
        """Render a stored clock timestamp as a 12-hour time, VERBATIM.

        TimeLog clock times are stored in the worker's LOCAL wall-clock time
        (the iOS app records local time; bulk imports synthesize local too),
        so we do NOT timezone-convert — a stored '07:29' is rendered '7:29 AM'.
        (Verified 2026-06-16 against real iOS data: Elmer Cordova 07:29→17:03 =
        9.56h, a normal Central workday. Treating these as UTC and converting
        shifted them ~5h, which is the bug this replaced.)
        """
        dt = cls._parse_dt(raw)
        if dt is None:
            return "—"
        # 12-hour, no leading zero, platform-independent.
        return dt.strftime("%I:%M %p").lstrip("0")

    @staticmethod
    def _format_date_long(work_date: str) -> str:
        """'2026-06-15' -> 'Monday, June 15, 2026'. Falls back to the raw value."""
        try:
            d = datetime.strptime(str(work_date), "%Y-%m-%d").date()
            return f"{d.strftime('%A, %B')} {d.day}, {d.year}"
        except Exception:
            return str(work_date)

    @staticmethod
    def _format_date_subject(work_date: str) -> str:
        """'2026-06-15' -> 'June 15, 2026' (no weekday). Falls back to raw."""
        try:
            d = datetime.strptime(str(work_date), "%Y-%m-%d").date()
            return f"{d.strftime('%B')} {d.day}, {d.year}"
        except Exception:
            return str(work_date)
