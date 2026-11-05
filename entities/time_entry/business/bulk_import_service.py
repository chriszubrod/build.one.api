# Python Standard Library Imports
import logging
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

# Local Imports
from entities.time_entry.business.service import TimeEntryService
from entities.time_entry.business.time_log_service import TimeLogService
from entities.user.business.service import UserService

logger = logging.getLogger(__name__)


class TimeEntryBulkImportService:
    """Format-agnostic bulk loader for historical time-tracking data.

    Takes a list of dicts (one per (worker, project, day) entry) and:
      1. Resolves Worker → User row (by public_id or firstname+lastname).
      2. Groups rows by (worker, work_date): ONE TimeEntry header per group,
         created in 'draft' status. If a header for that worker-day already
         exists outside this import (the worker's own app day, an earlier
         import, the office) every row of the group fails -- an import never
         merges into an existing header.
      3. Synthesizes one TimeLog per row under the header (ClockIn = WorkDate
         09:00 UTC + hours already placed in the group, ClockOut = ClockIn +
         hours).
      4. Submits the TimeEntry ONCE per group (if any row asks for it) —
         Phase 4 aggregation fires automatically, populating ContractLabor or
         EmployeeLabor based on User worker link.

    Excel + CSV adapters live in front of this service and convert their
    native row shape into the dict format below.

    Per-row dict shape (all string keys):
        worker_user_public_id   (str)  OR
        worker_firstname        (str) + worker_lastname (str)
        project_public_id       (str)  optional — NULL means "no project"
        work_date               (str)  ISO date "YYYY-MM-DD"
        hours                   (str|Decimal)  positive decimal
        note                    (str)  optional
        submit                  (bool) default True; pass False to leave draft
    """

    def __init__(self):
        self.entry_service = TimeEntryService()
        self.log_service = TimeLogService()
        self.user_service = UserService()

    @staticmethod
    def _coerce_decimal(value: Any) -> Decimal:
        if isinstance(value, Decimal):
            return value
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError) as e:
            raise ValueError(f"Invalid hours value: {value!r}") from e

    def _resolve_worker_user_public_id(self, row: dict) -> str:
        """Look up the User by either public_id or by name pair."""
        if row.get("worker_user_public_id"):
            user = self.user_service.read_by_public_id(public_id=row["worker_user_public_id"])
            if not user:
                raise ValueError(f"User public_id {row['worker_user_public_id']!r} not found.")
            return user.public_id

        firstname = (row.get("worker_firstname") or "").strip()
        lastname = (row.get("worker_lastname") or "").strip()
        if not firstname or not lastname:
            raise ValueError(
                "Row missing worker identity: pass worker_user_public_id OR "
                "both worker_firstname + worker_lastname."
            )

        # read_by_firstname is the closest match; tighten via service if it
        # turns out worker name collisions are common.
        user = self.user_service.read_by_firstname(firstname=firstname)
        if not user or (user.lastname or "").lower() != lastname.lower():
            raise ValueError(
                f"No User found matching '{firstname} {lastname}'. "
                "Confirm User row exists + lastname is exact."
            )
        return user.public_id

    def _synth_clock_times(
        self, work_date: str, hours: Decimal, start_offset_hours: Decimal = Decimal(0)
    ) -> tuple[str, str]:
        """ClockIn = work_date 09:00:00 + start_offset_hours; ClockOut = ClockIn + hours.

        Synthetic times are good enough for billing aggregation (the
        AggregateTimeEntryOnSubmit sproc only reads SUM(Duration), not the
        actual clock boundaries). The offset staggers several rows of one
        worker-day so they never collide on UX_TimeLog_TimeEntry_ClockIn. If
        precise times matter for a downstream consumer, replace this with the
        source-format's actual ClockIn/ClockOut.
        """
        dt = datetime.combine(datetime.fromisoformat(work_date).date(), time(9, 0))
        dt += timedelta(hours=float(start_offset_hours))
        clock_in = dt.isoformat(sep=" ", timespec="seconds")
        clock_out = (dt + timedelta(hours=float(hours))).isoformat(sep=" ", timespec="seconds")
        return clock_in, clock_out

    def _resolve_project_id(self, idx: int, row: dict) -> Optional[int]:
        if not row.get("project_public_id"):
            return None
        from entities.project.business.service import ProjectService
        project = ProjectService().read_by_public_id(public_id=row["project_public_id"])
        if project:
            return int(project.id)
        logger.warning(
            "bulk_import.project_not_found",
            extra={"row_index": idx, "project_public_id": row["project_public_id"]},
        )
        return None

    def import_rows(self, rows: list[dict]) -> list[dict]:
        """Process every row. Returns per-row result dicts:
            { row_index, status, time_entry_public_id?, error? }
        status ∈ {'created', 'submitted', 'failed'}.

        Rows are grouped by (resolved worker, work_date): one header per group.
        """
        results: dict[int, dict] = {}
        groups: dict[tuple[str, str], list[tuple[int, dict, Decimal]]] = {}

        # Phase 1: validate + resolve each row, bucket by (worker, day) in first-seen order.
        group_errors: dict[tuple[str, str], str] = {}
        worker_cache: dict[tuple, tuple[Optional[str], Optional[str]]] = {}
        for idx, row in enumerate(rows):
            try:
                identity = (
                    row.get("worker_user_public_id"),
                    (row.get("worker_firstname") or "").strip().lower(),
                    (row.get("worker_lastname") or "").strip().lower(),
                )
                if identity not in worker_cache:
                    try:
                        worker_cache[identity] = (self._resolve_worker_user_public_id(row), None)
                    except Exception as exc:  # resolution error fails every row of that worker
                        worker_cache[identity] = (None, str(exc))
                user_public_id, resolve_error = worker_cache[identity]
                if resolve_error is not None:
                    raise ValueError(resolve_error)
                work_date = row.get("work_date")
                if not work_date:
                    raise ValueError("Row missing work_date.")
                try:
                    work_date = datetime.fromisoformat(str(work_date)).date().isoformat()
                except ValueError:
                    raise ValueError("Row has an invalid work_date")
                hours = self._coerce_decimal(row.get("hours"))
                if hours <= 0:
                    raise ValueError(f"hours must be positive (got {hours}).")
                groups.setdefault((user_public_id, work_date), []).append((idx, row, hours))
            except Exception as exc:
                logger.exception("bulk_import.row.failed", extra={"row_index": idx})
                results[idx] = {"row_index": idx, "status": "failed", "error": str(exc)}

        # Phase 2: one header per group.
        for (user_public_id, work_date), members in groups.items():
            self._import_group(user_public_id, work_date, members, results)

        return [results[i] for i in sorted(results)]

    def _import_group(
        self,
        user_public_id: str,
        work_date: str,
        members: list[tuple[int, dict, Decimal]],
        results: dict[int, dict],
    ) -> None:
        def fail_all(message: str) -> None:
            for idx, _row, _hours in members:
                results[idx] = {"row_index": idx, "status": "failed", "error": message}

        try:
            # 1. Create the header in draft (find-or-create; first row's note)
            entry = self.entry_service.create(
                user_public_id=user_public_id,
                work_date=work_date,
                note=members[0][1].get("note"),
            )
        except Exception as exc:
            logger.exception("bulk_import.group.failed", extra={"work_date": work_date})
            fail_all(str(exc))
            return

        if entry.created is False:  # None = created by a legacy sproc body
            # Payroll-safe: merging into a live draft would double hours a worker also
            # logs; a submitted day is locked by design.
            fail_all(
                f"Worker already has a time entry for {work_date} (id {entry.id}); "
                "an import never merges into an existing header — resolve in the office."
            )
            return

        # 2. One TimeLog per row, staggered from 09:00 by the hours already placed
        placed = Decimal(0)
        landed: list[int] = []
        for idx, row, hours in members:
            try:
                project_id = self._resolve_project_id(idx, row)
                clock_in, clock_out = self._synth_clock_times(
                    work_date, hours, start_offset_hours=placed
                )
                self.log_service.create(
                    time_entry_public_id=entry.public_id,
                    clock_in=clock_in,
                    clock_out=clock_out,
                    log_type="work",
                    project_id=project_id,
                    note=row.get("note"),
                )
                placed += hours
                landed.append(idx)
            except Exception as exc:
                logger.exception("bulk_import.row.failed", extra={"row_index": idx})
                results[idx] = {"row_index": idx, "status": "failed", "error": str(exc)}

        left_in_draft = " — header left in draft (import created it)"

        if not landed:
            # Every log failed: an empty draft header remains; tell the operator.
            for idx, _row, _hours in members:
                prior = results[idx]["error"]
                results[idx] = {
                    "row_index": idx,
                    "status": "failed",
                    "time_entry_public_id": entry.public_id,
                    "error": prior + left_in_draft,
                }
            return

        # 3. Submit ONCE (default — caller can suppress for staging)
        status = "created"
        if any(row.get("submit", True) for idx, row, _h in members if idx in landed):
            try:
                self.entry_service.submit(public_id=entry.public_id, user_id=entry.user_id)
                status = "submitted"
            except Exception as exc:
                logger.exception("bulk_import.group.submit_failed", extra={"work_date": work_date})
                for idx in landed:
                    results[idx] = {
                        "row_index": idx,
                        "status": "failed",
                        "time_entry_public_id": entry.public_id,
                        "error": str(exc) + left_in_draft,
                    }
                return

        for idx in landed:
            results[idx] = {
                "row_index": idx,
                "status": status,
                "time_entry_public_id": entry.public_id,
            }
