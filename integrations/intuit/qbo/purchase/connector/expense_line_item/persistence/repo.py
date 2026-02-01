# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.purchase.connector.expense_line_item.business.model import PurchaseLineExpenseLineItem
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class PurchaseLineExpenseLineItemRepository:
    """
    Repository for PurchaseLineExpenseLineItem persistence operations.
    """

    def __init__(self):
        """Initialize the PurchaseLineExpenseLineItemRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[PurchaseLineExpenseLineItem]:
        """
        Convert a database row into a PurchaseLineExpenseLineItem dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return PurchaseLineExpenseLineItem(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                qbo_purchase_line_id=getattr(row, "QboPurchaseLineId", None),
                expense_line_item_id=getattr(row, "ExpenseLineItemId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during purchase line expense line item mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during purchase line expense line item mapping: {error}")
            raise map_database_error(error)

    def create(self, *, qbo_purchase_line_id: int, expense_line_item_id: int) -> PurchaseLineExpenseLineItem:
        """
        Create a new PurchaseLineExpenseLineItem mapping record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreatePurchaseLineExpenseLineItem",
                        params={
                            "QboPurchaseLineId": qbo_purchase_line_id,
                            "ExpenseLineItemId": expense_line_item_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreatePurchaseLineExpenseLineItem did not return a row.")
                        raise map_database_error(Exception("CreatePurchaseLineExpenseLineItem failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create purchase line expense line item: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[PurchaseLineExpenseLineItem]:
        """
        Read a PurchaseLineExpenseLineItem mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadPurchaseLineExpenseLineItemById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read purchase line expense line item by ID: {error}")
            raise map_database_error(error)

    def read_by_expense_line_item_id(self, expense_line_item_id: int) -> Optional[PurchaseLineExpenseLineItem]:
        """
        Read a PurchaseLineExpenseLineItem mapping record by ExpenseLineItem ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadPurchaseLineExpenseLineItemByExpenseLineItemId",
                        params={"ExpenseLineItemId": expense_line_item_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read purchase line expense line item by expense line item ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_purchase_line_id(self, qbo_purchase_line_id: int) -> Optional[PurchaseLineExpenseLineItem]:
        """
        Read a PurchaseLineExpenseLineItem mapping record by QboPurchaseLine ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadPurchaseLineExpenseLineItemByQboPurchaseLineId",
                        params={"QboPurchaseLineId": qbo_purchase_line_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read purchase line expense line item by QBO purchase line ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[PurchaseLineExpenseLineItem]:
        """
        Delete a PurchaseLineExpenseLineItem mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeletePurchaseLineExpenseLineItemById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during delete purchase line expense line item by ID: {error}")
            raise map_database_error(error)
