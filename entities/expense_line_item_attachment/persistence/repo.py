# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.expense_line_item_attachment.business.model import ExpenseLineItemAttachment
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class ExpenseLineItemAttachmentRepository:
    """
    Repository for ExpenseLineItemAttachment persistence operations.
    """

    def __init__(self):
        """Initialize the ExpenseLineItemAttachmentRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[ExpenseLineItemAttachment]:
        """
        Convert a database row into an ExpenseLineItemAttachment dataclass.
        """
        if not row:
            return None

        try:
            return ExpenseLineItemAttachment(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                expense_line_item_id=row.ExpenseLineItemId,
                attachment_id=row.AttachmentId,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during expense line item attachment mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during expense line item attachment mapping: {error}")
            raise map_database_error(error)

    def create(self, *, expense_line_item_id: int, attachment_id: int) -> ExpenseLineItemAttachment:
        """
        Create a new expense line item attachment.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateExpenseLineItemAttachment",
                        params={
                            "ExpenseLineItemId": expense_line_item_id,
                            "AttachmentId": attachment_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateExpenseLineItemAttachment did not return a row.")
                        raise map_database_error(Exception("CreateExpenseLineItemAttachment failed"))
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during create expense line item attachment: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[ExpenseLineItemAttachment]:
        """
        Read all expense line item attachments.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadExpenseLineItemAttachments",
                        params={},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read all expense line item attachments: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[ExpenseLineItemAttachment]:
        """
        Read an expense line item attachment by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadExpenseLineItemAttachmentById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read expense line item attachment by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[ExpenseLineItemAttachment]:
        """
        Read an expense line item attachment by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadExpenseLineItemAttachmentByPublicId",
                        params={"PublicId": public_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read expense line item attachment by public ID: {error}")
            raise map_database_error(error)

    def read_by_expense_line_item_id(self, expense_line_item_id: int) -> Optional[ExpenseLineItemAttachment]:
        """
        Read expense line item attachment by expense line item ID.
        Returns single attachment (1-1 relationship).
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadExpenseLineItemAttachmentByExpenseLineItemId",
                        params={"ExpenseLineItemId": expense_line_item_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read expense line item attachment by expense line item ID: {error}")
            raise map_database_error(error)

    def read_by_expense_line_item_public_ids(self, public_ids: list[str]) -> list[ExpenseLineItemAttachment]:
        """
        Read expense line item attachments for multiple expense line items by their public IDs.
        Returns all attachments for the given expense line item public IDs in a single query.
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
                        SELECT elia.Id, elia.PublicId, elia.RowVersion, elia.CreatedDatetime,
                               elia.ModifiedDatetime, elia.ExpenseLineItemId, elia.AttachmentId,
                               eli.PublicId AS ExpenseLineItemPublicId
                        FROM dbo.ExpenseLineItemAttachment elia
                        JOIN dbo.ExpenseLineItem eli ON eli.Id = elia.ExpenseLineItemId
                        WHERE eli.PublicId IN ({placeholders})
                    """
                    cursor.execute(query, public_ids)
                    rows = cursor.fetchall()
                    results = []
                    for row in rows:
                        if row:
                            attachment = self._from_db(row)
                            if attachment:
                                # Add the expense_line_item_public_id for mapping
                                attachment.expense_line_item_public_id = getattr(row, 'ExpenseLineItemPublicId', None)
                                results.append(attachment)
                    return results
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read expense line item attachments by public IDs: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[ExpenseLineItemAttachment]:
        """
        Delete an expense line item attachment by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteExpenseLineItemAttachmentById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during delete expense line item attachment by ID: {error}")
            raise map_database_error(error)
