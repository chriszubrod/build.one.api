# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.purchase.connector.expense.business.model import PurchaseExpense
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class PurchaseExpenseRepository:
    """
    Repository for PurchaseExpense persistence operations.
    """

    def __init__(self):
        """Initialize the PurchaseExpenseRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[PurchaseExpense]:
        """
        Convert a database row into a PurchaseExpense dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return PurchaseExpense(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                qbo_purchase_id=getattr(row, "QboPurchaseId", None),
                expense_id=getattr(row, "ExpenseId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during purchase expense mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during purchase expense mapping: {error}")
            raise map_database_error(error)

    def create(self, *, qbo_purchase_id: int, expense_id: int) -> PurchaseExpense:
        """
        Create a new PurchaseExpense mapping record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreatePurchaseExpense",
                        params={
                            "QboPurchaseId": qbo_purchase_id,
                            "ExpenseId": expense_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreatePurchaseExpense did not return a row.")
                        raise map_database_error(Exception("CreatePurchaseExpense failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create purchase expense: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[PurchaseExpense]:
        """
        Read a PurchaseExpense mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadPurchaseExpenseById",
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
            logger.error(f"Error during read purchase expense by ID: {error}")
            raise map_database_error(error)

    def read_by_expense_id(self, expense_id: int) -> Optional[PurchaseExpense]:
        """
        Read a PurchaseExpense mapping record by Expense ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadPurchaseExpenseByExpenseId",
                        params={"ExpenseId": expense_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read purchase expense by expense ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_purchase_id(self, qbo_purchase_id: int) -> Optional[PurchaseExpense]:
        """
        Read a PurchaseExpense mapping record by QboPurchase ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadPurchaseExpenseByQboPurchaseId",
                        params={"QboPurchaseId": qbo_purchase_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read purchase expense by QBO purchase ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[PurchaseExpense]:
        """
        Delete a PurchaseExpense mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeletePurchaseExpenseById",
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
            logger.error(f"Error during delete purchase expense by ID: {error}")
            raise map_database_error(error)
