# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.invoice_line_item_attachment.business.model import InvoiceLineItemAttachment
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class InvoiceLineItemAttachmentRepository:

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[InvoiceLineItemAttachment]:
        if not row:
            return None

        try:
            return InvoiceLineItemAttachment(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                invoice_line_item_id=row.InvoiceLineItemId,
                attachment_id=row.AttachmentId,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during invoice line item attachment mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during invoice line item attachment mapping: {error}")
            raise map_database_error(error)

    def create(self, *, invoice_line_item_id: int, attachment_id: int) -> InvoiceLineItemAttachment:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateInvoiceLineItemAttachment",
                        params={
                            "InvoiceLineItemId": invoice_line_item_id,
                            "AttachmentId": attachment_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateInvoiceLineItemAttachment did not return a row.")
                        raise map_database_error(Exception("CreateInvoiceLineItemAttachment failed"))
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during create invoice line item attachment: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[InvoiceLineItemAttachment]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadInvoiceLineItemAttachments",
                        params={},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read all invoice line item attachments: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[InvoiceLineItemAttachment]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadInvoiceLineItemAttachmentById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read invoice line item attachment by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[InvoiceLineItemAttachment]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadInvoiceLineItemAttachmentByPublicId",
                        params={"PublicId": public_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read invoice line item attachment by public ID: {error}")
            raise map_database_error(error)

    def read_by_invoice_line_item_id(self, invoice_line_item_id: int) -> list[InvoiceLineItemAttachment]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadInvoiceLineItemAttachmentsByInvoiceLineItemId",
                        params={"InvoiceLineItemId": invoice_line_item_id},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read invoice line item attachments by invoice line item ID: {error}")
            raise map_database_error(error)

    def read_by_invoice_line_item_public_ids(self, public_ids: list[str]) -> list[InvoiceLineItemAttachment]:
        if not public_ids:
            return []
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    placeholders = ",".join(["?" for _ in public_ids])
                    query = f"""
                        SELECT ilia.Id, ilia.PublicId, ilia.RowVersion, ilia.CreatedDatetime,
                               ilia.ModifiedDatetime, ilia.InvoiceLineItemId, ilia.AttachmentId,
                               ili.PublicId AS InvoiceLineItemPublicId
                        FROM dbo.InvoiceLineItemAttachment ilia
                        JOIN dbo.InvoiceLineItem ili ON ili.Id = ilia.InvoiceLineItemId
                        WHERE ili.PublicId IN ({placeholders})
                    """
                    cursor.execute(query, public_ids)
                    rows = cursor.fetchall()
                    results = []
                    for row in rows:
                        if row:
                            attachment = self._from_db(row)
                            if attachment:
                                attachment.invoice_line_item_public_id = getattr(row, 'InvoiceLineItemPublicId', None)
                                results.append(attachment)
                    return results
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read invoice line item attachments by public IDs: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[InvoiceLineItemAttachment]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteInvoiceLineItemAttachmentById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during delete invoice line item attachment by ID: {error}")
            raise map_database_error(error)
