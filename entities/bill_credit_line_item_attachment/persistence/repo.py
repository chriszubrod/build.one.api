# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.bill_credit_line_item_attachment.business.model import BillCreditLineItemAttachment
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class BillCreditLineItemAttachmentRepository:
    """
    Repository for BillCreditLineItemAttachment persistence operations.
    """

    def __init__(self):
        """Initialize the BillCreditLineItemAttachmentRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[BillCreditLineItemAttachment]:
        """
        Convert a database row into a BillCreditLineItemAttachment dataclass.
        """
        if not row:
            return None

        try:
            return BillCreditLineItemAttachment(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                bill_credit_line_item_id=row.BillCreditLineItemId,
                attachment_id=row.AttachmentId,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during bill credit line item attachment mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during bill credit line item attachment mapping: {error}")
            raise map_database_error(error)

    def create(self, *, bill_credit_line_item_id: int, attachment_id: int) -> BillCreditLineItemAttachment:
        """
        Create a new bill credit line item attachment.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateBillCreditLineItemAttachment",
                        params={
                            "BillCreditLineItemId": bill_credit_line_item_id,
                            "AttachmentId": attachment_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateBillCreditLineItemAttachment did not return a row.")
                        raise map_database_error(Exception("CreateBillCreditLineItemAttachment failed"))
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during create bill credit line item attachment: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[BillCreditLineItemAttachment]:
        """
        Read all bill credit line item attachments.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBillCreditLineItemAttachments",
                        params={},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read all bill credit line item attachments: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[BillCreditLineItemAttachment]:
        """
        Read a bill credit line item attachment by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBillCreditLineItemAttachmentById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read bill credit line item attachment by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[BillCreditLineItemAttachment]:
        """
        Read a bill credit line item attachment by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBillCreditLineItemAttachmentByPublicId",
                        params={"PublicId": public_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read bill credit line item attachment by public ID: {error}")
            raise map_database_error(error)

    def read_by_bill_credit_line_item_id(self, bill_credit_line_item_id: int) -> Optional[BillCreditLineItemAttachment]:
        """
        Read bill credit line item attachment by bill credit line item ID.
        Returns single attachment (1-1 relationship).
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBillCreditLineItemAttachmentByBillCreditLineItemId",
                        params={"BillCreditLineItemId": bill_credit_line_item_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read bill credit line item attachment by bill credit line item ID: {error}")
            raise map_database_error(error)

    def read_by_bill_credit_line_item_public_ids(self, public_ids: list[str]) -> list[BillCreditLineItemAttachment]:
        """
        Read bill credit line item attachments for multiple bill credit line items by their public IDs.
        Returns all attachments for the given bill credit line item public IDs in a single query.
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
                        SELECT bclia.Id, bclia.PublicId, bclia.RowVersion, bclia.CreatedDatetime,
                               bclia.ModifiedDatetime, bclia.BillCreditLineItemId, bclia.AttachmentId,
                               bcli.PublicId AS BillCreditLineItemPublicId
                        FROM dbo.BillCreditLineItemAttachment bclia
                        JOIN dbo.BillCreditLineItem bcli ON bcli.Id = bclia.BillCreditLineItemId
                        WHERE bcli.PublicId IN ({placeholders})
                    """
                    cursor.execute(query, public_ids)
                    rows = cursor.fetchall()
                    results = []
                    for row in rows:
                        if row:
                            attachment = self._from_db(row)
                            if attachment:
                                # Add the bill_credit_line_item_public_id for mapping
                                attachment.bill_credit_line_item_public_id = getattr(row, 'BillCreditLineItemPublicId', None)
                                results.append(attachment)
                    return results
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read bill credit line item attachments by public IDs: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[BillCreditLineItemAttachment]:
        """
        Delete a bill credit line item attachment by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteBillCreditLineItemAttachmentById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during delete bill credit line item attachment by ID: {error}")
            raise map_database_error(error)
