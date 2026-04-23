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
