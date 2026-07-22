# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.business_license_attachment.business.model import BusinessLicenseAttachment
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class BusinessLicenseAttachmentRepository:
    """
    Repository for BusinessLicenseAttachment persistence operations.
    """

    def __init__(self):
        """Initialize the BusinessLicenseAttachmentRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[BusinessLicenseAttachment]:
        """
        Convert a database row into a BusinessLicenseAttachment dataclass.
        """
        if not row:
            return None

        try:
            return BusinessLicenseAttachment(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                business_license_id=row.BusinessLicenseId,
                attachment_id=row.AttachmentId,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during business license attachment mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during business license attachment mapping: {error}")
            raise map_database_error(error)

    def create(self, *, business_license_id: int, attachment_id: int) -> BusinessLicenseAttachment:
        """
        Create a new business license attachment link.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateBusinessLicenseAttachment",
                        params={
                            "BusinessLicenseId": business_license_id,
                            "AttachmentId": attachment_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateBusinessLicenseAttachment did not return a row.")
                        raise map_database_error(Exception("CreateBusinessLicenseAttachment failed"))
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during create business license attachment: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[BusinessLicenseAttachment]:
        """
        Read all business license attachments.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBusinessLicenseAttachments",
                        params={},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read all business license attachments: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[BusinessLicenseAttachment]:
        """
        Read a business license attachment by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBusinessLicenseAttachmentById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read business license attachment by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[BusinessLicenseAttachment]:
        """
        Read a business license attachment by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBusinessLicenseAttachmentByPublicId",
                        params={"PublicId": public_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read business license attachment by public ID: {error}")
            raise map_database_error(error)

    def read_by_business_license_id(self, business_license_id: int) -> list[BusinessLicenseAttachment]:
        """
        Read business license attachments by business license ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBusinessLicenseAttachmentsByBusinessLicenseId",
                        params={"BusinessLicenseId": business_license_id},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read business license attachments by business license ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[BusinessLicenseAttachment]:
        """
        Delete a business license attachment by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteBusinessLicenseAttachmentById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during delete business license attachment by ID: {error}")
            raise map_database_error(error)
