# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.review_status.business.model import ReviewStatus
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class ReviewStatusRepository:
    """
    Repository for ReviewStatus persistence operations.
    """

    def __init__(self):
        """Initialize the ReviewStatusRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[ReviewStatus]:
        """
        Convert a database row into a ReviewStatus dataclass.
        """
        if not row:
            return None

        try:
            return ReviewStatus(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                name=row.Name,
                description=row.Description,
                sort_order=row.SortOrder,
                is_final=bool(row.IsFinal),
                is_declined=bool(row.IsDeclined),
                is_active=bool(row.IsActive),
                color=row.Color,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during review status mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during review status mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        name: Optional[str],
        description: Optional[str] = None,
        sort_order: int = 0,
        is_final: bool = False,
        is_declined: bool = False,
        is_active: bool = True,
        color: Optional[str] = None,
    ) -> ReviewStatus:
        """
        Create a new review status.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateReviewStatus",
                    params={
                        "Name": name,
                        "Description": description,
                        "SortOrder": sort_order,
                        "IsFinal": is_final,
                        "IsDeclined": is_declined,
                        "IsActive": is_active,
                        "Color": color,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateReviewStatus did not return a row.")
                    raise map_database_error(Exception("CreateReviewStatus failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create review status: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[ReviewStatus]:
        """
        Read all review statuses, ordered by SortOrder.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadReviewStatuses",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all review statuses: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[ReviewStatus]:
        """
        Read a review status by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadReviewStatusById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read review status by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[ReviewStatus]:
        """
        Read a review status by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadReviewStatusByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read review status by public ID: {error}")
            raise map_database_error(error)

    def read_next(self, current_sort_order: int) -> Optional[ReviewStatus]:
        """
        Read the next active, non-declined review status after the given sort order.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadNextReviewStatus",
                    params={"CurrentSortOrder": current_sort_order},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read next review status: {error}")
            raise map_database_error(error)

    def read_first(self) -> Optional[ReviewStatus]:
        """
        Read the first active, non-declined review status by sort order.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadFirstReviewStatus",
                    params={},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read first review status: {error}")
            raise map_database_error(error)

    def update_by_id(self, review_status: ReviewStatus) -> Optional[ReviewStatus]:
        """
        Update a review status by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateReviewStatusById",
                    params={
                        "Id": review_status.id,
                        "RowVersion": review_status.row_version_bytes,
                        "Name": review_status.name,
                        "Description": review_status.description,
                        "SortOrder": review_status.sort_order,
                        "IsFinal": review_status.is_final,
                        "IsDeclined": review_status.is_declined,
                        "IsActive": review_status.is_active,
                        "Color": review_status.color,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update review status by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[ReviewStatus]:
        """
        Delete a review status by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteReviewStatusById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete review status by ID: {error}")
            raise map_database_error(error)
