# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.time_entry.business.model import TimeEntryStatus
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class TimeEntryStatusRepository:
    """
    Repository for TimeEntryStatus persistence operations.
    """

    def __init__(self):
        """Initialize the TimeEntryStatusRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[TimeEntryStatus]:
        """
        Convert a database row into a TimeEntryStatus dataclass.
        """
        if not row:
            return None

        try:
            return TimeEntryStatus(
                id=row.Id,
                public_id=str(row.PublicId) if row.PublicId else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                time_entry_id=getattr(row, "TimeEntryId", None),
                status=getattr(row, "Status", None),
                user_id=getattr(row, "UserId", None),
                note=getattr(row, "Note", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during time entry status mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during time entry status mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        time_entry_id: int,
        status: str,
        user_id: int,
        note: Optional[str] = None,
    ) -> TimeEntryStatus:
        """
        Create a new time entry status record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateTimeEntryStatus",
                    params={
                        "TimeEntryId": time_entry_id,
                        "Status": status,
                        "UserId": user_id,
                        "Note": note,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateTimeEntryStatus did not return a row.")
                    raise map_database_error(Exception("CreateTimeEntryStatus failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create time entry status: {error}")
            raise map_database_error(error)

    def read_by_time_entry_id(self, time_entry_id: int) -> list[TimeEntryStatus]:
        """
        Read all status records for a specific time entry (full history).
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTimeEntryStatusesByTimeEntryId",
                    params={"TimeEntryId": time_entry_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read time entry statuses by time entry ID: {error}")
            raise map_database_error(error)

    def read_current(self, time_entry_id: int) -> Optional[TimeEntryStatus]:
        """
        Read the current (most recent) status for a time entry.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCurrentTimeEntryStatus",
                    params={"TimeEntryId": time_entry_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read current time entry status: {error}")
            raise map_database_error(error)
