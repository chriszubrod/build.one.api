# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from core.ai.agents.expense_agent.business.models import ExpenseAgentRun
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class ExpenseAgentRunRepository:
    """Repository for ExpenseAgentRun persistence operations."""

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[ExpenseAgentRun]:
        """Convert a database row into an ExpenseAgentRun dataclass."""
        if not row:
            return None

        try:
            return ExpenseAgentRun(
                id=row.Id,
                public_id=str(row.PublicId) if row.PublicId else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if row.RowVersion else None,
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                status=row.Status,
                trigger_source=row.TriggerSource,
                completed_datetime=row.CompletedDatetime,
                files_found=row.FilesFound or 0,
                files_processed=row.FilesProcessed or 0,
                files_skipped=row.FilesSkipped or 0,
                expenses_created=row.ExpensesCreated or 0,
                error_count=row.ErrorCount or 0,
                summary=row.Summary,
                created_by=row.CreatedBy,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during ExpenseAgentRun mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during ExpenseAgentRun mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        trigger_source: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> ExpenseAgentRun:
        """Create a new expense agent run record."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "TriggerSource": trigger_source,
                    "CreatedBy": created_by,
                }
                call_procedure(cursor=cursor, name="CreateExpenseAgentRun", params=params)
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateExpenseAgentRun did not return a row.")
                    raise map_database_error(Exception("CreateExpenseAgentRun failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create expense agent run: {error}")
            raise map_database_error(error)

    def complete(
        self,
        public_id: str,
        *,
        files_found: int = 0,
        files_processed: int = 0,
        files_skipped: int = 0,
        expenses_created: int = 0,
        error_count: int = 0,
        summary: Optional[str] = None,
    ) -> Optional[ExpenseAgentRun]:
        """Mark a run as completed with metrics."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "PublicId": public_id,
                    "FilesFound": files_found,
                    "FilesProcessed": files_processed,
                    "FilesSkipped": files_skipped,
                    "ExpensesCreated": expenses_created,
                    "ErrorCount": error_count,
                    "Summary": summary,
                }
                call_procedure(cursor=cursor, name="CompleteExpenseAgentRun", params=params)
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during complete expense agent run: {error}")
            raise map_database_error(error)

    def fail(
        self,
        public_id: str,
        *,
        summary: Optional[str] = None,
    ) -> Optional[ExpenseAgentRun]:
        """Mark a run as failed."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "PublicId": public_id,
                    "Summary": summary,
                }
                call_procedure(cursor=cursor, name="FailExpenseAgentRun", params=params)
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during fail expense agent run: {error}")
            raise map_database_error(error)

    def update_progress(
        self,
        public_id: str,
        *,
        files_found: int = 0,
        files_processed: int = 0,
        files_skipped: int = 0,
        expenses_created: int = 0,
        error_count: int = 0,
    ) -> None:
        """Update progress metrics on a running run (no row returned)."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "PublicId": public_id,
                    "FilesFound": files_found,
                    "FilesProcessed": files_processed,
                    "FilesSkipped": files_skipped,
                    "ExpensesCreated": expenses_created,
                    "ErrorCount": error_count,
                }
                call_procedure(cursor=cursor, name="UpdateExpenseAgentRunProgress", params=params)
        except Exception as error:
            logger.warning(f"Error updating expense agent run progress: {error}")

    def read_by_public_id(self, public_id: str) -> Optional[ExpenseAgentRun]:
        """Read a run by public ID."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadExpenseAgentRunByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read expense agent run by public ID: {error}")
            raise map_database_error(error)

    def read_recent(self, limit: int = 20) -> list[ExpenseAgentRun]:
        """Read recent expense agent runs."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadRecentExpenseAgentRuns",
                    params={"Limit": limit},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read recent expense agent runs: {error}")
            raise map_database_error(error)
