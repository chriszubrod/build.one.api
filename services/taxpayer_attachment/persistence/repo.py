# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from services.taxpayer_attachment.business.model import TaxpayerAttachment
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class TaxpayerAttachmentRepository:
    """
    Repository for TaxpayerAttachment persistence operations.
    """

    def __init__(self):
        """Initialize the TaxpayerAttachmentRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[TaxpayerAttachment]:
        """
        Convert a database row into a TaxpayerAttachment dataclass.
        """
        if not row:
            return None

        try:
            return TaxpayerAttachment(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                taxpayer_id=row.TaxpayerId,
                attachment_id=row.AttachmentId,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during taxpayer attachment mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during taxpayer attachment mapping: {error}")
            raise map_database_error(error)

    def create(self, *, taxpayer_id: int, attachment_id: int) -> TaxpayerAttachment:
        """
        Create a new taxpayer attachment.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateTaxpayerAttachment",
                        params={
                            "TaxpayerId": taxpayer_id,
                            "AttachmentId": attachment_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateTaxpayerAttachment did not return a row.")
                        raise map_database_error(Exception("CreateTaxpayerAttachment failed"))
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during create taxpayer attachment: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[TaxpayerAttachment]:
        """
        Read all taxpayer attachments.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadTaxpayerAttachments",
                        params={},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read all taxpayer attachments: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[TaxpayerAttachment]:
        """
        Read a taxpayer attachment by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadTaxpayerAttachmentById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read taxpayer attachment by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[TaxpayerAttachment]:
        """
        Read a taxpayer attachment by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadTaxpayerAttachmentByPublicId",
                        params={"PublicId": public_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read taxpayer attachment by public ID: {error}")
            raise map_database_error(error)

    def read_by_taxpayer_id(self, taxpayer_id: int) -> list[TaxpayerAttachment]:
        """
        Read taxpayer attachments by taxpayer ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadTaxpayerAttachmentsByTaxpayerId",
                        params={"TaxpayerId": taxpayer_id},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read taxpayer attachments by taxpayer ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[TaxpayerAttachment]:
        """
        Delete a taxpayer attachment by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteTaxpayerAttachmentById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during delete taxpayer attachment by ID: {error}")
            raise map_database_error(error)

