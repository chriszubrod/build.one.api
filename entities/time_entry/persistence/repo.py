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
                project_id=getattr(row, "ProjectId", None),
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
        project_id: int,
        work_date: str,
        note: Optional[str] = None,
    ) -> TimeEntry:
        """
        Create a new time entry.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateTimeEntry",
                    params={
                        "UserId": user_id,
                        "ProjectId": project_id,
                        "WorkDate": work_date,
                        "Note": note,
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

    def read_all(self) -> list[TimeEntry]:
        """
        Read all time entries.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTimeEntries",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all time entries: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[TimeEntry]:
        """
        Read a time entry by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTimeEntryById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read time entry by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[TimeEntry]:
        """
        Read a time entry by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTimeEntryByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read time entry by public ID: {error}")
            raise map_database_error(error)

    def read_by_user_id(self, user_id: int) -> list[TimeEntry]:
        """
        Read all time entries for a specific user.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTimeEntriesByUserId",
                    params={"UserId": user_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read time entries by user ID: {error}")
            raise map_database_error(error)

    def read_by_project_id(self, project_id: int) -> list[TimeEntry]:
        """
        Read all time entries for a specific project.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadTimeEntriesByProjectId",
                    params={"ProjectId": project_id},
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
    ) -> list[TimeEntry]:
        """
        Read time entries with pagination and filtering.
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
    ) -> int:
        """
        Count time entries matching the filter criteria.
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
                    },
                )
                row = cursor.fetchone()
                return row.TotalCount if row else 0
        except Exception as error:
            logger.error(f"Error during count time entries: {error}")
            raise map_database_error(error)

    def update_by_id(self, time_entry: TimeEntry) -> Optional[TimeEntry]:
        """
        Update a time entry by ID.
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
                        "ProjectId": time_entry.project_id,
                        "WorkDate": time_entry.work_date,
                        "Note": time_entry.note,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.warning(
                        "UpdateTimeEntryById returned no row (id=%s); possible row-version conflict or record not found.",
                        time_entry.id,
                    )
                    raise map_database_error(
                        Exception(
                            "Update did not match any row; the time entry may have been modified by another process (row-version conflict) or no longer exists."
                        )
                    )
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update time entry by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[TimeEntry]:
        """
        Delete a time entry by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteTimeEntryById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete time entry by ID: {error}")
            raise map_database_error(error)

    def delete_by_public_id(self, public_id: str) -> Optional[TimeEntry]:
        """
        Delete a time entry by public ID.
        """
        try:
            entry = self.read_by_public_id(public_id=public_id)
            if not entry:
                return None
            return self.delete_by_id(id=entry.id)
        except Exception as error:
            logger.error(f"Error during delete time entry by public ID: {error}")
            raise map_database_error(error)
