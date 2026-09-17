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
from shared.lifecycle.terminal_lock import reraise_if_sproc_status_locked

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

    def create(self, *, expense_line_item_id: int, attachment_id: int, created_by_user_id: Optional[int] = None, allow_terminal_parent: bool = True) -> ExpenseLineItemAttachment:
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
                            "CreatedByUserId": created_by_user_id,
                            "AllowTerminalParent": 1 if allow_terminal_parent else 0,
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
            reraise_if_sproc_status_locked(
                error, what="attachments cannot be added to it"
            )
            logger.error(f"Error during create expense line item attachment: {error}")
            raise map_database_error(error)

    def read_all(
        self,
        *,
        actor_user_id: Optional[int] = None,
        actor_is_system_admin: Optional[bool] = None,
    ) -> list[ExpenseLineItemAttachment]:
        """
        Read expense line item attachments, scoped by UserProject membership
        for non-admin actors. The sproc fails closed: an actor of (None, None)
        matches no rows rather than every row.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadExpenseLineItemAttachments",
                        params={
                            "ActorUserId": actor_user_id,
                            "ActorIsSystemAdmin": _bit(actor_is_system_admin),
                        },
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
                    # Pass as comma-separated string — sproc uses STRING_SPLIT internally
                    ids_csv = ",".join(str(pid) for pid in public_ids)
                    call_procedure(
                        cursor=cursor,
                        name="ReadExpenseLineItemAttachmentsByExpenseLineItemPublicIds",
                        params={"PublicIds": ids_csv},
                    )
                    rows = cursor.fetchall()
                    results = []
                    for row in rows:
                        if row:
                            attachment = self._from_db(row)
                            if attachment:
                                attachment.expense_line_item_public_id = getattr(row, "ExpenseLineItemPublicId", None)
                                results.append(attachment)
                    return results
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during read expense line item attachments by public IDs: {error}")
            raise map_database_error(error)

    def count_completed_expenses_by_attachment_id(self, attachment_id: int) -> int:
        """U-468: how many COMPLETED Expenses this Attachment is evidence for.

        Non-zero means the file is frozen — the AP it documents has already
        reached QBO, SharePoint, Excel and Box, so replacing or deleting it
        changes what our records show without changing what any of them hold.
        Mirrors `BillLineItemAttachmentRepository.count_completed_bills_by_attachment_id`.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CountCompletedExpensesByAttachmentId",
                        params={"AttachmentId": attachment_id},
                    )
                    row = cursor.fetchone()
                    return row.Count if row else 0
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error counting completed expenses by attachment ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int, *, allow_terminal_parent: bool = True) -> Optional[ExpenseLineItemAttachment]:
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
                        params={
                            "Id": id,
                            "AllowTerminalParent": 1 if allow_terminal_parent else 0,
                        },
                    )
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    cursor.close()
        except Exception as error:
            reraise_if_sproc_status_locked(
                error, what="its attachments cannot be deleted"
            )
            logger.error(f"Error during delete expense line item attachment by ID: {error}")
            raise map_database_error(error)


def _bit(flag: Optional[bool]) -> Optional[int]:
    """SQL Server BIT params take 0/1, not Python bool. Local copy per the
    prevailing per-repo convention; consolidation tracked separately."""
    if flag is None:
        return None
    return 1 if flag else 0

