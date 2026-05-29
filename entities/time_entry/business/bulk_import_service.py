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
      2. Creates a TimeEntry in 'draft' status.
      3. Synthesizes a single TimeLog under it (ClockIn = WorkDate 09:00 UTC,
         ClockOut = ClockIn + hours).
      4. Submits the TimeEntry — Phase 4 aggregation fires automatically,
         populating ContractLabor or EmployeeLabor based on User worker link.

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

    def _synth_clock_times(self, work_date: str, hours: Decimal) -> tuple[str, str]:
        """ClockIn = work_date 09:00:00; ClockOut = ClockIn + hours.

        Synthetic times are good enough for billing aggregation (the
        AggregateTimeEntryOnSubmit sproc only reads SUM(Duration), not the
        actual clock boundaries). If precise times matter for a downstream
        consumer, replace this with the source-format's actual ClockIn/ClockOut.
        """
        dt = datetime.combine(datetime.fromisoformat(work_date).date(), time(9, 0))
        clock_in = dt.isoformat(sep=" ", timespec="seconds")
        clock_out = (dt + timedelta(hours=float(hours))).isoformat(sep=" ", timespec="seconds")
        return clock_in, clock_out

    def import_rows(self, rows: list[dict]) -> list[dict]:
        """Process every row. Returns per-row result dicts:
            { row_index, status, time_entry_public_id?, error? }
        status ∈ {'created', 'submitted', 'failed'}.
        """
        results: list[dict] = []

        for idx, row in enumerate(rows):
            try:
                user_public_id = self._resolve_worker_user_public_id(row)
                work_date = row.get("work_date")
                if not work_date:
                    raise ValueError("Row missing work_date.")
                hours = self._coerce_decimal(row.get("hours"))
                if hours <= 0:
                    raise ValueError(f"hours must be positive (got {hours}).")

                # 1. Create TimeEntry in draft
                entry = self.entry_service.create(
                    user_public_id=user_public_id,
                    work_date=work_date,
                    note=row.get("note"),
                )

                # 2. Resolve project
                project_id = None
                if row.get("project_public_id"):
                    from entities.project.business.service import ProjectService
                    project = ProjectService().read_by_public_id(public_id=row["project_public_id"])
                    if project:
                        project_id = int(project.id)
                    else:
                        logger.warning(
                            "bulk_import.project_not_found",
                            extra={"row_index": idx, "project_public_id": row["project_public_id"]},
                        )

                # 3. Synthesize TimeLog
                clock_in, clock_out = self._synth_clock_times(work_date, hours)
                self.log_service.create(
                    time_entry_public_id=entry.public_id,
                    clock_in=clock_in,
                    clock_out=clock_out,
                    log_type="work",
                    project_id=project_id,
                    note=row.get("note"),
                )

                # 4. Submit (default — caller can suppress for staging)
                if row.get("submit", True):
                    self.entry_service.submit(
                        public_id=entry.public_id,
                        user_id=entry.user_id,
                    )
                    status = "submitted"
                else:
                    status = "created"

                results.append({
                    "row_index": idx,
                    "status": status,
                    "time_entry_public_id": entry.public_id,
                })
            except Exception as exc:
                logger.exception("bulk_import.row.failed", extra={"row_index": idx})
                results.append({
                    "row_index": idx,
                    "status": "failed",
                    "error": str(exc),
                })

        return results
