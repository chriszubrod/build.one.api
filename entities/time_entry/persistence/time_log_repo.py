# Python Standard Library Imports
import base64
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports
import pyodbc

# Local Imports
from entities.time_entry.business.model import TimeLog
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class TimeLogRepository:
    """
    Repository for TimeLog persistence operations.
    """

    def __init__(self):
        """Initialize the TimeLogRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[TimeLog]:
        """
        Convert a database row into a TimeLog dataclass.
        """
        if not row:
            return None

        try:
            return TimeLog(
                id=row.Id,
                public_id=str(row.PublicId) if row.PublicId else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                time_entry_id=getattr(row, "TimeEntryId", None),
                clock_in=getattr(row, "ClockIn", None),
                clock_out=getattr(row, "ClockOut", None),
                log_type=getattr(row, "LogType", None),
                duration=Decimal(str(getattr(row, "Duration", None))) if getattr(row, "Duration", None) is not None else None,
                latitude=Decimal(str(getattr(row, "Latitude", None))) if getattr(row, "Latitude", None) is not None else None,
                longitude=Decimal(str(getattr(row, "Longitude", None))) if getattr(row, "Longitude", None) is not None else None,
                project_id=getattr(row, "ProjectId", None),
                note=getattr(row, "Note", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during time log mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during time log mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        time_entry_id: int,
        clock_in: str,
        clock_out: Optional[str] = None,
        log_type: str = "work",
        duration: Optional[Decimal] = None,
        latitude: Optional[Decimal] = None,
        longitude: Optional[Decimal] = None,
        project_id: Optional[int] = None,
        note: Optional[str] = None,
    ) -> TimeLog:
        """
        Create a new time log.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateTimeLog",
                    params={
                        "TimeEntryId": time_entry_id,
                        "ClockIn": clock_in,
                        "ClockOut": clock_out,
                        "LogType": log_type,
                        "Duration": Decimal(str(duration)) if duration is not None else None,
                        "Latitude": Decimal(str(latitude)) if latitude is not None else None,
                        "Longitude": Decimal(str(longitude)) if longitude is not None else None,
                        "ProjectId": project_id,
                        "Note": note,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateTimeLog did not return a row.")
                    raise map_database_error(Exception("CreateTimeLog failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create time log: {error}")
            raise map_database_error(error)

    def read_by_time_entry_id(self, time_entry_id: int) -> list[TimeLog]:
        """
        Read all time logs for a specific time entry.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTimeLogsByTimeEntryId",
                    params={"TimeEntryId": time_entry_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read time logs by time entry ID: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[TimeLog]:
        """
        Read a time log by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTimeLogById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read time log by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[TimeLog]:
        """
        Read a time log by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTimeLogByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read time log by public ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, time_log: TimeLog) -> Optional[TimeLog]:
        """
        Update a time log by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateTimeLogById",
                    params={
                        "Id": time_log.id,
                        "RowVersion": time_log.row_version_bytes,
                        "ClockIn": time_log.clock_in,
                        "ClockOut": time_log.clock_out,
                        "LogType": time_log.log_type,
                        "Duration": Decimal(str(time_log.duration)) if time_log.duration is not None else None,
                        "Latitude": Decimal(str(time_log.latitude)) if time_log.latitude is not None else None,
                        "Longitude": Decimal(str(time_log.longitude)) if time_log.longitude is not None else None,
                        "ProjectId": time_log.project_id,
                        "Note": time_log.note,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.warning(
                        "UpdateTimeLogById returned no row (id=%s); possible row-version conflict or record not found.",
                        time_log.id,
                    )
                    raise map_database_error(
                        Exception(
                            "Update did not match any row; the time log may have been modified by another process (row-version conflict) or no longer exists."
                        )
                    )
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update time log by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[TimeLog]:
        """
        Delete a time log by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteTimeLogById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete time log by ID: {error}")
            raise map_database_error(error)

    def delete_by_public_id(self, public_id: str) -> Optional[TimeLog]:
        """
        Delete a time log by public ID.
        """
        try:
            log = self.read_by_public_id(public_id=public_id)
            if not log:
                return None
            return self.delete_by_id(id=log.id)
        except Exception as error:
            logger.error(f"Error during delete time log by public ID: {error}")
            raise map_database_error(error)
