"""
Run-state tracker for the Process Folder flow. Shared across gunicorn
workers so the POST (which creates a run) and the polling GET (which
reads its status) see the same row regardless of which worker served
them. Replaces the in-process dict that broke under -w 2.
"""
import json
import logging
from dataclasses import dataclass
from typing import Optional

import pyodbc

from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


@dataclass
class BillFolderRun:
    public_id: str
    status: str                      # "processing" | "completed" | "failed"
    result: Optional[dict]           # parsed JSON payload (files_found, bills_created, errors, ...)
    started_at: Optional[str]
    completed_at: Optional[str]


class BillFolderRunRepository:
    """Minimal repo for BillFolderRun."""

    def _from_db(self, row: Optional[pyodbc.Row]) -> Optional[BillFolderRun]:
        if not row:
            return None
        raw_result = getattr(row, "Result", None)
        parsed_result: Optional[dict] = None
        if raw_result:
            try:
                parsed_result = json.loads(raw_result)
            except (TypeError, ValueError):
                logger.warning("BillFolderRun %s has invalid JSON in Result", row.PublicId)
        return BillFolderRun(
            public_id=str(row.PublicId),
            status=row.Status,
            result=parsed_result,
            started_at=row.StartedAt,
            completed_at=row.CompletedAt,
        )

    def create(self, public_id: Optional[str] = None) -> BillFolderRun:
        """Insert a new run in status='processing'. Returns the stored row."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateBillFolderRun",
                    params={"PublicId": public_id, "Status": "processing"},
                )
                row = cursor.fetchone()
                run = self._from_db(row)
                if run is None:
                    raise RuntimeError("CreateBillFolderRun returned no row")
                return run
        except Exception as error:
            logger.error(f"Error creating bill folder run: {error}")
            raise map_database_error(error)

    def update_status(
        self,
        public_id: str,
        status: str,
        result: Optional[dict] = None,
        set_completed: bool = False,
    ) -> None:
        """Set the run's terminal status + JSON result payload."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateBillFolderRunByPublicId",
                    params={
                        "PublicId": public_id,
                        "Status": status,
                        "Result": json.dumps(result) if result is not None else None,
                        "SetCompleted": 1 if set_completed else 0,
                    },
                )
        except Exception as error:
            logger.error(f"Error updating bill folder run {public_id}: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[BillFolderRun]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillFolderRunByPublicId",
                    params={"PublicId": public_id},
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error reading bill folder run {public_id}: {error}")
            raise map_database_error(error)


# =============================================================================
# Per-file work items (one row per PDF queued under a run).
# =============================================================================


@dataclass
class BillFolderRunItem:
    id: int
    public_id: str
    run_id: int
    filename: str
    item_id: str                     # SharePoint drive-item id
    status: str                      # queued | processing | completed | skipped | failed
    attempts: int


@dataclass
class BillFolderRunAggregate:
    run_id: int
    run_public_id: str
    run_status: str
    started_at: Optional[str]
    completed_at: Optional[str]
    files_total: int
    files_queued: int
    files_processing: int
    files_processed: int
    files_skipped: int
    files_failed: int
    current_file: Optional[str]


@dataclass
class BillFolderRunItemError:
    filename: str
    status: str
    last_error: Optional[str]
    attempts: int


class BillFolderRunItemRepository:
    """Claim-and-update wrapper around the BillFolderRunItem sprocs."""

    def _from_claim_row(self, row: Optional[pyodbc.Row]) -> Optional[BillFolderRunItem]:
        if not row:
            return None
        return BillFolderRunItem(
            id=row.Id,
            public_id=str(row.PublicId),
            run_id=row.RunId,
            filename=row.Filename,
            item_id=row.ItemId,
            status=row.Status,
            attempts=row.Attempts,
        )

    def _from_create_row(self, row: Optional[pyodbc.Row]) -> Optional[BillFolderRunItem]:
        # Create sproc returns a subset without ClaimedAt/StartedAt; reuse
        # the same dataclass since those columns aren't on the dataclass.
        return self._from_claim_row(row)

    def create(self, run_id: int, filename: str, item_id: str) -> BillFolderRunItem:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateBillFolderRunItem",
                    params={"RunId": run_id, "Filename": filename, "ItemId": item_id},
                )
                row = cursor.fetchone()
                item = self._from_create_row(row)
                if item is None:
                    raise RuntimeError("CreateBillFolderRunItem returned no row")
                return item
        except Exception as error:
            logger.error(f"Error creating bill folder run item: {error}")
            raise map_database_error(error)

    def claim_next(
        self,
        reclaim_after_seconds: int = 180,
        max_attempts: int = 3,
    ) -> Optional[BillFolderRunItem]:
        """Claim one queued (or abandoned-processing) item. Returns None when idle."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ClaimNextBillFolderRunItem",
                    params={
                        "ReclaimAfterSeconds": reclaim_after_seconds,
                        "MaxAttempts": max_attempts,
                    },
                )
                row = cursor.fetchone()
                return self._from_claim_row(row)
        except Exception as error:
            logger.error(f"Error claiming bill folder run item: {error}")
            raise map_database_error(error)

    def mark_success(self, public_id: str, status: str, result: Optional[dict] = None) -> None:
        """Mark an item terminal ('completed' or 'skipped')."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateBillFolderRunItemOnSuccess",
                    params={
                        "PublicId": public_id,
                        "Status": status,
                        "Result": json.dumps(result) if result is not None else None,
                    },
                )
        except Exception as error:
            logger.error(f"Error marking item {public_id} success: {error}")
            raise map_database_error(error)

    def mark_failure(self, public_id: str, last_error: str, max_attempts: int = 3) -> None:
        """Return an item to 'queued' for retry, or mark permanently 'failed' at max attempts."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateBillFolderRunItemOnFailure",
                    params={
                        "PublicId": public_id,
                        "LastError": last_error,
                        "MaxAttempts": max_attempts,
                    },
                )
        except Exception as error:
            logger.error(f"Error marking item {public_id} failure: {error}")
            raise map_database_error(error)

    def check_and_complete_run(self, run_id: int) -> None:
        """If the parent run has no more open items, mark it 'completed'."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CheckAndCompleteBillFolderRun",
                    params={"RunId": run_id},
                )
        except Exception as error:
            logger.error(f"Error check-and-completing run {run_id}: {error}")
            raise map_database_error(error)

    def read_aggregate(self, run_public_id: str) -> Optional[BillFolderRunAggregate]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillFolderRunAggregateByPublicId",
                    params={"RunPublicId": run_public_id},
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return BillFolderRunAggregate(
                    run_id=row.RunId,
                    run_public_id=str(row.RunPublicId),
                    run_status=row.RunStatus,
                    started_at=row.StartedAt,
                    completed_at=row.CompletedAt,
                    files_total=row.FilesTotal or 0,
                    files_queued=row.FilesQueued or 0,
                    files_processing=row.FilesProcessing or 0,
                    files_processed=row.FilesProcessed or 0,
                    files_skipped=row.FilesSkipped or 0,
                    files_failed=row.FilesFailed or 0,
                    current_file=row.CurrentFile,
                )
        except Exception as error:
            logger.error(f"Error reading aggregate for run {run_public_id}: {error}")
            raise map_database_error(error)

    def read_errors(self, run_public_id: str) -> list[BillFolderRunItemError]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillFolderRunItemErrorsByRunPublicId",
                    params={"RunPublicId": run_public_id},
                )
                rows = cursor.fetchall()
                return [
                    BillFolderRunItemError(
                        filename=r.Filename,
                        status=r.Status,
                        last_error=r.LastError,
                        attempts=r.Attempts,
                    )
                    for r in rows
                ]
        except Exception as error:
            logger.error(f"Error reading errors for run {run_public_id}: {error}")
            raise map_database_error(error)

    def auto_fail_stale(self, stale_after_minutes: int = 30) -> None:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="AutoFailStaleBillFolderRuns",
                    params={"StaleAfterMinutes": stale_after_minutes},
                )
        except Exception as error:
            logger.error(f"Error auto-failing stale runs: {error}")
            raise map_database_error(error)
