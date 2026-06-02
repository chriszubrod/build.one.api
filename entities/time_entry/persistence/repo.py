# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.time_entry.business.model import TimeEntry
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class TimeEntryRepository:
    """
    Repository for TimeEntry persistence operations.

    Phase 3 row-scoping: every read/update/delete forwards
    `actor_user_id` and `actor_is_system_admin` to the sproc layer.
    Sprocs filter to rows where TimeEntry.UserId = actor_user_id
    (system admin bypasses, NULL bypasses for legacy back-compat).
    """

    def __init__(self):
        """Initialize the TimeEntryRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[TimeEntry]:
        """
        Convert a database row into a TimeEntry dataclass.
        """
        if not row:
            return None

        try:
            return TimeEntry(
                id=row.Id,
                public_id=str(row.PublicId) if row.PublicId else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                user_id=getattr(row, "UserId", None),
                work_date=getattr(row, "WorkDate", None),
                note=getattr(row, "Note", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during time entry mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during time entry mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        user_id: int,
        work_date: str,
        note: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
    ) -> TimeEntry:
        """
        Create a new time entry. Create paths are unscoped — the
        service layer prevents impersonation at the API surface.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateTimeEntry",
                    params={
                        "UserId": user_id,
                        "WorkDate": work_date,
                        "Note": note,
                        "CreatedByUserId": created_by_user_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateTimeEntry did not return a row.")
                    raise map_database_error(Exception("CreateTimeEntry failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create time entry: {error}")
            raise map_database_error(error)

    def read_all(
        self,
        *,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
        actor_can_view_team: Optional[bool] = False,
    ) ->list[TimeEntry]:
        """
        Read time entries, scoped to the actor.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTimeEntries",
                    params={
                        "ActorUserId": actor_user_id,
                        "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                        "ActorCanViewTeam": _bit(actor_can_view_team),
                    },
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all time entries: {error}")
            raise map_database_error(error)

    def read_by_id(
        self,
        id: int,
        *,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
        actor_can_view_team: Optional[bool] = False,
    ) ->Optional[TimeEntry]:
        """
        Read a time entry by ID, scoped to the actor.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTimeEntryById",
                    params={
                        "Id": id,
                        "ActorUserId": actor_user_id,
                        "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                        "ActorCanViewTeam": _bit(actor_can_view_team),
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read time entry by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(
        self,
        public_id: str,
        *,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
        actor_can_view_team: Optional[bool] = False,
    ) ->Optional[TimeEntry]:
        """
        Read a time entry by public ID, scoped to the actor.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTimeEntryByPublicId",
                    params={
                        "PublicId": public_id,
                        "ActorUserId": actor_user_id,
                        "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                        "ActorCanViewTeam": _bit(actor_can_view_team),
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read time entry by public ID: {error}")
            raise map_database_error(error)

    def read_by_user_id(
        self,
        user_id: int,
        *,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
        actor_can_view_team: Optional[bool] = False,
    ) ->list[TimeEntry]:
        """
        Read all time entries for a specific user, scoped to the actor.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTimeEntriesByUserId",
                    params={
                        "UserId": user_id,
                        "ActorUserId": actor_user_id,
                        "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                        "ActorCanViewTeam": _bit(actor_can_view_team),
                    },
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read time entries by user ID: {error}")
            raise map_database_error(error)

    def read_by_project_id(
        self,
        project_id: int,
        *,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
        actor_can_view_team: Optional[bool] = False,
    ) ->list[TimeEntry]:
        """
        Read all time entries for a specific project, scoped to the actor.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTimeEntriesByProjectId",
                    params={
                        "ProjectId": project_id,
                        "ActorUserId": actor_user_id,
                        "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                        "ActorCanViewTeam": _bit(actor_can_view_team),
                    },
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read time entries by project ID: {error}")
            raise map_database_error(error)

    def read_paginated(
        self,
        *,
        page_number: int = 1,
        page_size: int = 50,
        search_term: Optional[str] = None,
        user_id: Optional[int] = None,
        project_id: Optional[int] = None,
        status: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sort_by: str = "WorkDate",
        sort_direction: str = "DESC",
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
        actor_can_view_team: Optional[bool] = False,
    ) ->list[TimeEntry]:
        """
        Read time entries with pagination and filtering, scoped to the actor.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTimeEntriesPaginated",
                    params={
                        "PageNumber": page_number,
                        "PageSize": page_size,
                        "SearchTerm": search_term,
                        "UserId": user_id,
                        "ProjectId": project_id,
                        "Status": status,
                        "StartDate": start_date,
                        "EndDate": end_date,
                        "SortBy": sort_by,
                        "SortDirection": sort_direction,
                        "ActorUserId": actor_user_id,
                        "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                        "ActorCanViewTeam": _bit(actor_can_view_team),
                    },
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read time entries paginated: {error}")
            raise map_database_error(error)

    def count(
        self,
        *,
        search_term: Optional[str] = None,
        user_id: Optional[int] = None,
        project_id: Optional[int] = None,
        status: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
        actor_can_view_team: Optional[bool] = False,
    ) ->int:
        """
        Count time entries matching the filter criteria, scoped to the actor.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CountTimeEntries",
                    params={
                        "SearchTerm": search_term,
                        "UserId": user_id,
                        "ProjectId": project_id,
                        "Status": status,
                        "StartDate": start_date,
                        "EndDate": end_date,
                        "ActorUserId": actor_user_id,
                        "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                        "ActorCanViewTeam": _bit(actor_can_view_team),
                    },
                )
                row = cursor.fetchone()
                return row.TotalCount if row else 0
        except Exception as error:
            logger.error(f"Error during count time entries: {error}")
            raise map_database_error(error)

    def update_by_id(
        self,
        time_entry: TimeEntry,
        *,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
        actor_can_view_team: Optional[bool] = False,
    ) ->Optional[TimeEntry]:
        """
        Update a time entry by ID, scoped to the actor.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateTimeEntryById",
                    params={
                        "Id": time_entry.id,
                        "RowVersion": time_entry.row_version_bytes,
                        "UserId": time_entry.user_id,
                        "WorkDate": time_entry.work_date,
                        "Note": time_entry.note,
                        "ActorUserId": actor_user_id,
                        "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                        "ActorCanViewTeam": _bit(actor_can_view_team),
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.warning(
                        "UpdateTimeEntryById returned no row (id=%s); possible row-version conflict, scope mismatch, or record not found.",
                        time_entry.id,
                    )
                    raise map_database_error(
                        Exception(
                            "Update did not match any row; the time entry may have been modified by another process (row-version conflict), is not accessible to this user, or no longer exists."
                        )
                    )
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update time entry by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(
        self,
        id: int,
        *,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
        actor_can_view_team: Optional[bool] = False,
    ) ->Optional[TimeEntry]:
        """
        Delete a time entry by ID, scoped to the actor.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteTimeEntryById",
                    params={
                        "Id": id,
                        "ActorUserId": actor_user_id,
                        "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                        "ActorCanViewTeam": _bit(actor_can_view_team),
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete time entry by ID: {error}")
            raise map_database_error(error)

    def delete_by_public_id(
        self,
        public_id: str,
        *,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
        actor_can_view_team: Optional[bool] = False,
    ) ->Optional[TimeEntry]:
        """
        Delete a time entry by public ID, scoped to the actor.
        """
        try:
            entry = self.read_by_public_id(
                public_id=public_id,
                actor_user_id=actor_user_id,
                actor_is_system_admin=actor_is_system_admin,
            )
            if not entry:
                return None
            return self.delete_by_id(
                id=entry.id,
                actor_user_id=actor_user_id,
                actor_is_system_admin=actor_is_system_admin,
            )
        except Exception as error:
            logger.error(f"Error during delete time entry by public ID: {error}")
            raise map_database_error(error)


    def read_billed_lineage(self, *, time_entry_id: int) -> list[dict]:
        """Return downstream ContractLabor + EmployeeLabor rows linked to
        this TimeEntry via SourceTimeEntryId, with their Bill/Invoice
        linkage when present. Used by the React TimeEntryView Lineage panel.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTimeEntryBilledLineage",
                    params={"TimeEntryId": time_entry_id},
                )
                out: list[dict] = []
                for r in cursor.fetchall():
                    out.append({
                        "target_table": r.TargetTable,
                        "target_id": r.TargetId,
                        "target_public_id": r.TargetPublicId,
                        "labor_status": r.LaborStatus,
                        "work_date": r.WorkDate,
                        "worker_id": r.WorkerId,
                        "worker_name": r.WorkerName,
                        "total_amount": str(r.TotalAmount) if r.TotalAmount is not None else None,
                        "linked_target_table": r.LinkedTargetTable if r.LinkedTargetId is not None else None,
                        "linked_target_id": r.LinkedTargetId,
                        "linked_target_public_id": r.LinkedTargetPublicId,
                        "linked_target_number": r.LinkedTargetNumber,
                    })
                return out
        except Exception as error:
            logger.error(f"Error during ReadTimeEntryBilledLineage (TimeEntryId={time_entry_id}): {error}")
            raise map_database_error(error)

    def is_downstream_locked(self, *, time_entry_id: int) -> bool:
        """True if any downstream aggregated row has hit a terminal state
        (ContractLabor.Status='billed' or EmployeeLabor.Status='invoiced')
        with this TimeEntry as its SourceTimeEntryId.

        Used by service.update_by_public_id + service.reject to block edits
        that would desync from a posted bill/invoice. Phase 5 guard.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="IsTimeEntryDownstreamLocked",
                    params={"TimeEntryId": time_entry_id},
                )
                row = cursor.fetchone()
                return bool(row.Locked) if row else False
        except Exception as error:
            # Surface as False on lookup failure — we'd rather allow an
            # edit than block one silently due to a sproc bug. The
            # aggregation sproc itself has its own "frozen — skipped"
            # protection that catches the actual desync case.
            logger.warning(
                "time_entry.is_downstream_locked.failed",
                extra={"time_entry_id": time_entry_id, "error": str(error)},
            )
            return False

    def aggregate_for_billing(self, *, time_entry_id: int) -> list[dict]:
        """Fire dbo.AggregateTimeEntryOnSubmit for the given TimeEntry.

        Returns the per-(Project, WorkDate) result rows the sproc surfaces:
        TargetTable / TargetRowId / ProjectId / WorkDate / TotalHours /
        HourlyRate / Markup / RateSource / Status / Note.

        Failure modes (sproc raises):
          - TimeEntry not found
          - User.EmployeeId and VendorId both NULL (worker not configured)
          - Both set (XOR violated)
        Caller (service.submit) catches and logs — does NOT roll back the
        submitted-status transition (best-effort sidecar, same pattern as
        the time_tracking_specialist outbox enqueue).
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="AggregateTimeEntryOnSubmit",
                    params={"TimeEntryId": time_entry_id},
                )
                rows = cursor.fetchall()
                return [
                    {
                        "target_table": r.TargetTable,
                        "target_row_id": r.TargetRowId,
                        "project_id": r.ProjectId,
                        "work_date": r.WorkDate,
                        "total_hours": float(r.TotalHours) if r.TotalHours is not None else None,
                        "hourly_rate": float(r.HourlyRate) if r.HourlyRate is not None else None,
                        "markup": float(r.Markup) if r.Markup is not None else None,
                        "rate_source": r.RateSource,
                        "status": r.Status,
                        "note": r.Note,
                    }
                    for r in rows
                ]
        except Exception as error:
            logger.error(f"Error during AggregateTimeEntryOnSubmit (TimeEntryId={time_entry_id}): {error}")
            raise map_database_error(error)

    def stamp_review(
        self,
        *,
        public_id: str,
        priority: str,
        reasons_json: str,
    ) -> int:
        """
        Stamp ReviewPriority + ReviewReasons on a TimeEntry by PublicId.

        Calls `dbo.StampTimeEntryReview` (idempotent UPDATE). Does NOT
        transition CurrentStatus, does NOT write a Workflow row — this is
        observability metadata. Returns the affected row count (0 means
        no row matched the PublicId).

        No actor scope: authorization is enforced at the endpoint layer
        (Modules.TIME_TRACKING can_update). The agent runs with its own
        JWT which carries that grant.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="StampTimeEntryReview",
                    params={
                        "TimeEntryPublicId": public_id,
                        "Priority": priority,
                        "ReasonsJson": reasons_json,
                    },
                )
                row = cursor.fetchone()
                return int(row[0]) if row else 0
        except Exception as error:
            logger.error(f"Error during stamp time entry review: {error}")
            raise map_database_error(error)


def _bit(flag: Optional[bool]) -> Optional[int]:
    """SQL Server BIT params take 0/1, not Python bool."""
    if flag is None:
        return None
    return 1 if flag else 0
