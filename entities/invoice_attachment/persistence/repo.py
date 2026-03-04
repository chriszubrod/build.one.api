# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.invoice_attachment.business.model import InvoiceAttachment
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class InvoiceAttachmentRepository:
    """
    Repository for InvoiceAttachment persistence operations.
    """

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[InvoiceAttachment]:
        if not row:
            return None

        try:
            return InvoiceAttachment(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                invoice_id=row.InvoiceId,
                attachment_id=row.AttachmentId,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during invoice attachment mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during invoice attachment mapping: {error}")
            raise map_database_error(error)

    def create(self, *, invoice_id: int, attachment_id: int) -> InvoiceAttachment:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateInvoiceAttachment",
                        params={
                            "InvoiceId": invoice_id,
                            "AttachmentId": attachment_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateInvoiceAttachment did not return a row.")
                        raise map_database_error(Exception("CreateInvoiceAttachment failed"))
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during create invoice attachment: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[InvoiceAttachment]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(cursor=cursor, name="ReadInvoiceAttachments", params={})
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read all invoice attachments: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[InvoiceAttachment]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(cursor=cursor, name="ReadInvoiceAttachmentById", params={"Id": id})
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read invoice attachment by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[InvoiceAttachment]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(cursor=cursor, name="ReadInvoiceAttachmentByPublicId", params={"PublicId": public_id})
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read invoice attachment by public ID: {error}")
            raise map_database_error(error)

    def read_by_invoice_id(self, invoice_id: int) -> list[InvoiceAttachment]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(cursor=cursor, name="ReadInvoiceAttachmentsByInvoiceId", params={"InvoiceId": invoice_id})
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read invoice attachments by invoice ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[InvoiceAttachment]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(cursor=cursor, name="DeleteInvoiceAttachmentById", params={"Id": id})
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during delete invoice attachment by ID: {error}")
            raise map_database_error(error)
