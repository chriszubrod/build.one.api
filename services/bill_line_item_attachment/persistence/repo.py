# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from services.bill_line_item_attachment.business.model import BillLineItemAttachment
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class BillLineItemAttachmentRepository:
    """
    Repository for BillLineItemAttachment persistence operations.
    """

    def __init__(self):
        """Initialize the BillLineItemAttachmentRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[BillLineItemAttachment]:
        """
        Convert a database row into a BillLineItemAttachment dataclass.
        """
        if not row:
            return None

        try:
            return BillLineItemAttachment(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                bill_line_item_id=row.BillLineItemId,
                attachment_id=row.AttachmentId,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during bill line item attachment mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during bill line item attachment mapping: {error}")
            raise map_database_error(error)

    def create(self, *, bill_line_item_id: int, attachment_id: int) -> BillLineItemAttachment:
        """
        Create a new bill line item attachment.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateBillLineItemAttachment",
                        params={
                            "BillLineItemId": bill_line_item_id,
                            "AttachmentId": attachment_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateBillLineItemAttachment did not return a row.")
                        raise map_database_error(Exception("CreateBillLineItemAttachment failed"))
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during create bill line item attachment: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[BillLineItemAttachment]:
        """
        Read all bill line item attachments.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBillLineItemAttachments",
                        params={},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read all bill line item attachments: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[BillLineItemAttachment]:
        """
        Read a bill line item attachment by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBillLineItemAttachmentById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read bill line item attachment by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[BillLineItemAttachment]:
        """
        Read a bill line item attachment by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBillLineItemAttachmentByPublicId",
                        params={"PublicId": public_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read bill line item attachment by public ID: {error}")
            raise map_database_error(error)

    def read_by_bill_line_item_id(self, bill_line_item_id: int) -> Optional[BillLineItemAttachment]:
        """
        Read bill line item attachment by bill line item ID.
        Returns single attachment (1-1 relationship).
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBillLineItemAttachmentByBillLineItemId",
                        params={"BillLineItemId": bill_line_item_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read bill line item attachment by bill line item ID: {error}")
            raise map_database_error(error)

    def read_by_bill_line_item_public_ids(self, public_ids: list[str]) -> list[BillLineItemAttachment]:
        """
        Read bill line item attachments for multiple bill line items by their public IDs.
        Returns all attachments for the given bill line item public IDs in a single query.
        """
        if not public_ids:
            return []
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    # Build IN clause with placeholders
                    placeholders = ",".join(["?" for _ in public_ids])
                    query = f"""
                        SELECT blia.Id, blia.PublicId, blia.RowVersion, blia.CreatedDatetime,
                               blia.ModifiedDatetime, blia.BillLineItemId, blia.AttachmentId,
                               bli.PublicId AS BillLineItemPublicId
                        FROM dbo.BillLineItemAttachment blia
                        JOIN dbo.BillLineItem bli ON bli.Id = blia.BillLineItemId
                        WHERE bli.PublicId IN ({placeholders})
                    """
                    cursor.execute(query, public_ids)
                    rows = cursor.fetchall()
                    results = []
                    for row in rows:
                        if row:
                            attachment = self._from_db(row)
                            if attachment:
                                # Add the bill_line_item_public_id for mapping
                                attachment.bill_line_item_public_id = getattr(row, 'BillLineItemPublicId', None)
                                results.append(attachment)
                    return results
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read bill line item attachments by public IDs: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[BillLineItemAttachment]:
        """
        Delete a bill line item attachment by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteBillLineItemAttachmentById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during delete bill line item attachment by ID: {error}")
            raise map_database_error(error)
