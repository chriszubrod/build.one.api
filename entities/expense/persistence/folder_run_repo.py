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
class ExpenseFolderRun:
    id: int                          # internal numeric PK; needed by ExpenseFolderRunItem.RunId FK
    public_id: str
    status: str                      # "processing" | "completed" | "failed"
    result: Optional[dict]           # parsed JSON payload (files_found, expenses_created, errors, ...)
    started_at: Optional[str]
    completed_at: Optional[str]


class ExpenseFolderRunRepository:
    """Minimal repo for ExpenseFolderRun."""

    def _from_db(self, row: Optional[pyodbc.Row]) -> Optional[ExpenseFolderRun]:
        if not row:
            return None
        raw_result = getattr(row, "Result", None)
        parsed_result: Optional[dict] = None
        if raw_result:
            try:
                parsed_result = json.loads(raw_result)
            except (TypeError, ValueError):
                logger.warning("ExpenseFolderRun %s has invalid JSON in Result", row.PublicId)
        return ExpenseFolderRun(
            id=row.Id,
            public_id=str(row.PublicId),
            status=row.Status,
            result=parsed_result,
            started_at=row.StartedAt,
            completed_at=row.CompletedAt,
        )

    def create(self, public_id: Optional[str] = None, created_by_user_id: Optional[int] = None) -> ExpenseFolderRun:
        """Insert a new run in status='processing'. Returns the stored row."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateExpenseFolderRun",
                    params={"PublicId": public_id, "Status": "processing", "CreatedByUserId": created_by_user_id},
                )
                row = cursor.fetchone()
                run = self._from_db(row)
                if run is None:
                    raise RuntimeError("CreateExpenseFolderRun returned no row")
                return run
        except Exception as error:
            logger.error(f"Error creating expense folder run: {error}")
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
                    name="UpdateExpenseFolderRunByPublicId",
                    params={
                        "PublicId": public_id,
                        "Status": status,
                        "Result": json.dumps(result) if result is not None else None,
                        "SetCompleted": 1 if set_completed else 0,
                    },
                )
        except Exception as error:
            logger.error(f"Error updating expense folder run {public_id}: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[ExpenseFolderRun]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadExpenseFolderRunByPublicId",
                    params={"PublicId": public_id},
                )
                return self._from_db(cursor.fetchone())
        except Exception as error:
            logger.error(f"Error reading expense folder run {public_id}: {error}")
            raise map_database_error(error)


# =============================================================================
# Per-file work items (one row per PDF queued under a run).
# =============================================================================


@dataclass
class ExpenseFolderRunItem:
    id: int
    public_id: str
    run_id: int
    filename: str
    item_id: str                     # SharePoint drive-item id
    status: str                      # queued | processing | completed | skipped | failed
    attempts: int


@dataclass
class ExpenseFolderRunAggregate:
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
class ExpenseFolderRunItemError:
    filename: str
    status: str
    last_error: Optional[str]
    attempts: int


class ExpenseFolderRunItemRepository:
    """Claim-and-update wrapper around the ExpenseFolderRunItem sprocs."""

    def _from_claim_row(self, row: Optional[pyodbc.Row]) -> Optional[ExpenseFolderRunItem]:
        if not row:
            return None
        return ExpenseFolderRunItem(
            id=row.Id,
            public_id=str(row.PublicId),
            run_id=row.RunId,
            filename=row.Filename,
            item_id=row.ItemId,
            status=row.Status,
            attempts=row.Attempts,
        )

    def _from_create_row(self, row: Optional[pyodbc.Row]) -> Optional[ExpenseFolderRunItem]:
        # Create sproc returns a subset without ClaimedAt/StartedAt; reuse
        # the same dataclass since those columns aren't on the dataclass.
        return self._from_claim_row(row)

    def create(self, run_id: int, filename: str, item_id: str, created_by_user_id: Optional[int] = None) -> ExpenseFolderRunItem:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateExpenseFolderRunItem",
                    params={"RunId": run_id, "Filename": filename, "ItemId": item_id, "CreatedByUserId": created_by_user_id},
                )
                row = cursor.fetchone()
                item = self._from_create_row(row)
                if item is None:
                    raise RuntimeError("CreateExpenseFolderRunItem returned no row")
                return item
        except Exception as error:
            logger.error(f"Error creating expense folder run item: {error}")
            raise map_database_error(error)

    def claim_next(
        self,
        reclaim_after_seconds: int = 180,
        max_attempts: int = 3,
    ) -> Optional[ExpenseFolderRunItem]:
        """Claim one queued (or abandoned-processing) item. Returns None when idle."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ClaimNextExpenseFolderRunItem",
                    params={
                        "ReclaimAfterSeconds": reclaim_after_seconds,
                        "MaxAttempts": max_attempts,
                    },
                )
                row = cursor.fetchone()
                return self._from_claim_row(row)
        except Exception as error:
            logger.error(f"Error claiming expense folder run item: {error}")
            raise map_database_error(error)

    def mark_success(self, public_id: str, status: str, result: Optional[dict] = None) -> None:
        """Mark an item terminal ('completed' or 'skipped')."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateExpenseFolderRunItemOnSuccess",
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
                    name="UpdateExpenseFolderRunItemOnFailure",
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
                    name="CheckAndCompleteExpenseFolderRun",
                    params={"RunId": run_id},
                )
        except Exception as error:
            logger.error(f"Error check-and-completing run {run_id}: {error}")
            raise map_database_error(error)

    def read_aggregate(self, run_public_id: str) -> Optional[ExpenseFolderRunAggregate]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadExpenseFolderRunAggregateByPublicId",
                    params={"RunPublicId": run_public_id},
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return ExpenseFolderRunAggregate(
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

    def read_errors(self, run_public_id: str) -> list[ExpenseFolderRunItemError]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadExpenseFolderRunItemErrorsByRunPublicId",
                    params={"RunPublicId": run_public_id},
                )
                rows = cursor.fetchall()
                return [
                    ExpenseFolderRunItemError(
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

    def read_active_item_ids(self, recent_window_minutes: int = 60) -> set[str]:
        """
        Return the SharePoint item_ids the scheduled enumerator should
        skip — anything currently 'queued'/'processing', plus anything
        attempted within the last `recent_window_minutes` (so files that
        keep failing don't churn through a new queue row every tick).
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadActiveExpenseFolderRunItemIds",
                    params={"RecentWindowMinutes": recent_window_minutes},
                )
                return {row.ItemId for row in cursor.fetchall()}
        except Exception as error:
            logger.error(f"Error reading active item ids: {error}")
            raise map_database_error(error)

    def auto_fail_stale(self, stale_after_minutes: int = 30) -> None:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="AutoFailStaleExpenseFolderRuns",
                    params={"StaleAfterMinutes": stale_after_minutes},
                )
        except Exception as error:
            logger.error(f"Error auto-failing stale runs: {error}")
            raise map_database_error(error)
