# Python Standard Library Imports
import base64
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports
import pyodbc

# Local Imports
from services.expense_line_item.business.model import ExpenseLineItem
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class ExpenseLineItemRepository:
    """
    Repository for ExpenseLineItem persistence operations.
    """

    def __init__(self):
        """Initialize the ExpenseLineItemRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[ExpenseLineItem]:
        """
        Convert a database row into an ExpenseLineItem dataclass.
        """
        if not row:
            return None

        try:
            return ExpenseLineItem(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                expense_id=getattr(row, "ExpenseId", None),
                sub_cost_code_id=getattr(row, "SubCostCodeId", None),
                project_id=getattr(row, "ProjectId", None),
                description=getattr(row, "Description", None),
                quantity=int(getattr(row, "Quantity", None)) if getattr(row, "Quantity", None) is not None else None,
                rate=Decimal(str(getattr(row, "Rate", None))) if getattr(row, "Rate", None) is not None else None,
                amount=Decimal(str(getattr(row, "Amount", None))) if getattr(row, "Amount", None) is not None else None,
                is_billable=bool(getattr(row, "IsBillable", False)) if getattr(row, "IsBillable", None) is not None else None,
                is_billed=bool(getattr(row, "IsBilled", False)) if getattr(row, "IsBilled", None) is not None else None,
                markup=Decimal(str(getattr(row, "Markup", None))) if getattr(row, "Markup", None) is not None else None,
                price=Decimal(str(getattr(row, "Price", None))) if getattr(row, "Price", None) is not None else None,
                is_draft=bool(getattr(row, "IsDraft", True)) if getattr(row, "IsDraft", None) is not None else None,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during expense line item mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during expense line item mapping: {error}")
            raise map_database_error(error)

    def create(self, *, expense_id: int, sub_cost_code_id: Optional[int] = None, project_id: Optional[int] = None, description: Optional[str] = None, quantity: Optional[int] = None, rate: Optional[Decimal] = None, amount: Optional[Decimal] = None, is_billable: Optional[bool] = None, is_billed: Optional[bool] = None, markup: Optional[Decimal] = None, price: Optional[Decimal] = None, is_draft: bool = True) -> ExpenseLineItem:
        """
        Create a new expense line item.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateExpenseLineItem",
                    params={
                        "ExpenseId": expense_id,
                        "SubCostCodeId": sub_cost_code_id,
                        "ProjectId": project_id,
                        "Description": description,
                        "Quantity": quantity,
                        "Rate": float(rate) if rate is not None else None,
                        "Amount": float(amount) if amount is not None else None,
                        "IsBillable": 1 if is_billable else 0 if is_billable is not None else None,
                        "IsBilled": 1 if is_billed else 0 if is_billed is not None else None,
                        "Markup": float(markup) if markup is not None else None,
                        "Price": float(price) if price is not None else None,
                        "IsDraft": 1 if is_draft else 0,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateExpenseLineItem did not return a row.")
                    raise map_database_error(Exception("CreateExpenseLineItem failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create expense line item: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[ExpenseLineItem]:
        """
        Read all expense line items.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadExpenseLineItems",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all expense line items: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[ExpenseLineItem]:
        """
        Read an expense line item by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadExpenseLineItemById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read expense line item by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[ExpenseLineItem]:
        """
        Read an expense line item by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadExpenseLineItemByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read expense line item by public ID: {error}")
            raise map_database_error(error)

    def read_by_expense_id(self, expense_id: int) -> list[ExpenseLineItem]:
        """
        Read all expense line items for a specific expense.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadExpenseLineItemsByExpenseId",
                    params={"ExpenseId": expense_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read expense line items by expense ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, expense_line_item: ExpenseLineItem) -> Optional[ExpenseLineItem]:
        """
        Update an expense line item by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "Id": expense_line_item.id,
                    "RowVersion": expense_line_item.row_version_bytes,
                    "ExpenseId": expense_line_item.expense_id,
                    "SubCostCodeId": expense_line_item.sub_cost_code_id,
                    "ProjectId": expense_line_item.project_id,
                    "Description": expense_line_item.description,
                    "Quantity": expense_line_item.quantity,
                    "Rate": float(expense_line_item.rate) if expense_line_item.rate is not None else None,
                    "Amount": float(expense_line_item.amount) if expense_line_item.amount is not None else None,
                    "IsBillable": 1 if expense_line_item.is_billable else 0 if expense_line_item.is_billable is not None else None,
                    "IsBilled": 1 if expense_line_item.is_billed else 0 if expense_line_item.is_billed is not None else None,
                    "Markup": float(expense_line_item.markup) if expense_line_item.markup is not None else None,
                    "Price": float(expense_line_item.price) if expense_line_item.price is not None else None,
                }
                # Only include IsDraft if it's explicitly set (not None)
                if expense_line_item.is_draft is not None:
                    params["IsDraft"] = 1 if expense_line_item.is_draft else 0
                call_procedure(
                    cursor=cursor,
                    name="UpdateExpenseLineItemById",
                    params=params,
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update expense line item by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[ExpenseLineItem]:
        """
        Delete an expense line item by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteExpenseLineItemById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete expense line item by ID: {error}")
            raise map_database_error(error)
