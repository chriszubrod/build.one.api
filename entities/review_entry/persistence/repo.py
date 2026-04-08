# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.review_entry.business.model import ReviewEntry
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class ReviewEntryRepository:
    """
    Repository for ReviewEntry persistence operations.
    """

    def __init__(self):
        """Initialize the ReviewEntryRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[ReviewEntry]:
        """
        Convert a database row into a ReviewEntry dataclass.
        Handles both basic rows (from Create/Update/Delete) and
        enriched rows (from Read procedures with JOINs).
        """
        if not row:
            return None

        try:
            return ReviewEntry(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                review_status_id=row.ReviewStatusId,
                bill_id=row.BillId,
                user_id=row.UserId,
                comments=row.Comments,
                status_name=getattr(row, "StatusName", None),
                status_sort_order=getattr(row, "StatusSortOrder", None),
                status_is_final=bool(getattr(row, "StatusIsFinal", False)) if getattr(row, "StatusIsFinal", None) is not None else None,
                status_is_declined=bool(getattr(row, "StatusIsDeclined", False)) if getattr(row, "StatusIsDeclined", None) is not None else None,
                status_color=getattr(row, "StatusColor", None),
                user_firstname=getattr(row, "UserFirstname", None),
                user_lastname=getattr(row, "UserLastname", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during review entry mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during review entry mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        review_status_id: int,
        bill_id: Optional[int] = None,
        user_id: Optional[int] = None,
        comments: Optional[str] = None,
    ) -> ReviewEntry:
        """
        Create a new review entry.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateReviewEntry",
                    params={
                        "ReviewStatusId": review_status_id,
                        "BillId": bill_id,
                        "UserId": user_id,
                        "Comments": comments,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateReviewEntry did not return a row.")
                    raise map_database_error(Exception("CreateReviewEntry failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create review entry: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[ReviewEntry]:
        """
        Read all review entries.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadReviewEntries",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all review entries: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[ReviewEntry]:
        """
        Read a review entry by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadReviewEntryById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read review entry by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[ReviewEntry]:
        """
        Read a review entry by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadReviewEntryByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read review entry by public ID: {error}")
            raise map_database_error(error)

    def read_by_bill_id(self, bill_id: int) -> list[ReviewEntry]:
        """
        Read all review entries for a bill, ordered by CreatedDatetime DESC.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadReviewEntriesByBillId",
                    params={"BillId": bill_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read review entries by bill ID: {error}")
            raise map_database_error(error)

    def read_latest_by_bill_id(self, bill_id: int) -> Optional[ReviewEntry]:
        """
        Read the latest review entry for a bill.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadLatestReviewEntryByBillId",
                    params={"BillId": bill_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read latest review entry by bill ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, review_entry: ReviewEntry) -> Optional[ReviewEntry]:
        """
        Update a review entry by ID (only Comments is editable).
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateReviewEntryById",
                    params={
                        "Id": review_entry.id,
                        "RowVersion": review_entry.row_version_bytes,
                        "Comments": review_entry.comments,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update review entry by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[ReviewEntry]:
        """
        Delete a review entry by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteReviewEntryById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete review entry by ID: {error}")
            raise map_database_error(error)

    def delete_by_bill_id(self, bill_id: int) -> None:
        """
        Delete all review entries for a bill (cascade cleanup).
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteReviewEntriesByBillId",
                    params={"BillId": bill_id},
                )
        except Exception as error:
            logger.error(f"Error during delete review entries by bill ID: {error}")
            raise map_database_error(error)
