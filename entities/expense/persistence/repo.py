# Python Standard Library Imports
import base64
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports
import pyodbc

# Local Imports
from entities.expense.business.model import Expense
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class ExpenseRepository:
    """
    Repository for Expense persistence operations.
    """

    def __init__(self):
        """Initialize the ExpenseRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Expense]:
        """
        Convert a database row into an Expense dataclass.
        """
        if not row:
            return None

        try:
            return Expense(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                vendor_id=getattr(row, "VendorId", None),
                expense_date=getattr(row, "ExpenseDate", None),
                reference_number=getattr(row, "ReferenceNumber", None),
                total_amount=Decimal(str(getattr(row, "TotalAmount", None))) if getattr(row, "TotalAmount", None) is not None else None,
                memo=getattr(row, "Memo", None),
                is_draft=bool(getattr(row, "IsDraft", False)) if getattr(row, "IsDraft", None) is not None else None,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during expense mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during expense mapping: {error}")
            raise map_database_error(error)

    def create(self, *, tenant_id: int = 1, vendor_id: Optional[int] = None, expense_date: Optional[str] = None, reference_number: Optional[str] = None, total_amount: Optional[Decimal] = None, memo: Optional[str] = None, is_draft: bool = True) -> Expense:
        """
        Create a new expense.
        
        Args:
            tenant_id: Tenant ID for multi-tenant isolation (logged for audit, not yet used for filtering)
            vendor_id: Vendor ID
            expense_date: Expense date
            reference_number: Reference number
            total_amount: Total amount
            memo: Memo
            is_draft: Whether expense is in draft state
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateExpense",
                    params={
                        "VendorId": vendor_id,
                        "ExpenseDate": expense_date,
                        "ReferenceNumber": reference_number,
                        "TotalAmount": float(total_amount) if total_amount is not None else None,
                        "Memo": memo,
                        "IsDraft": 1 if is_draft else 0,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateExpense did not return a row.")
                    raise map_database_error(Exception("CreateExpense failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create expense: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[Expense]:
        """
        Read all expenses.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadExpenses",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all expenses: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[Expense]:
        """
        Read an expense by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadExpenseById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read expense by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Expense]:
        """
        Read an expense by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadExpenseByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read expense by public ID: {error}")
            raise map_database_error(error)

    def read_by_reference_number_and_vendor_id(self, reference_number: str, vendor_id: int) -> Optional[Expense]:
        """
        Read an expense by reference number and vendor ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadExpenseByReferenceNumberAndVendorId",
                    params={
                        "ReferenceNumber": reference_number,
                        "VendorId": vendor_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read expense by reference number and vendor ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, expense: Expense) -> Optional[Expense]:
        """
        Update an expense by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "Id": expense.id,
                    "RowVersion": expense.row_version_bytes,
                    "VendorId": expense.vendor_id,
                    "ExpenseDate": expense.expense_date,
                    "ReferenceNumber": expense.reference_number,
                    "TotalAmount": float(expense.total_amount) if expense.total_amount is not None else None,
                    "Memo": expense.memo,
                }
                # Only include IsDraft if it's explicitly set (not None)
                if expense.is_draft is not None:
                    params["IsDraft"] = 1 if expense.is_draft else 0
                
                call_procedure(
                    cursor=cursor,
                    name="UpdateExpenseById",
                    params=params,
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update expense by ID: {error}")
            raise map_database_error(error)

    def read_paginated(
        self,
        *,
        page_number: int = 1,
        page_size: int = 50,
        search_term: Optional[str] = None,
        vendor_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        is_draft: Optional[bool] = None,
        sort_by: str = "ExpenseDate",
        sort_direction: str = "DESC",
    ) -> list[Expense]:
        """
        Read expenses with pagination and filtering.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "PageNumber": page_number,
                    "PageSize": page_size,
                    "SearchTerm": search_term,
                    "VendorId": vendor_id,
                    "StartDate": start_date,
                    "EndDate": end_date,
                    "IsDraft": 1 if is_draft else (0 if is_draft is False else None),
                    "SortBy": sort_by,
                    "SortDirection": sort_direction,
                }
                call_procedure(
                    cursor=cursor,
                    name="ReadExpensesPaginated",
                    params=params,
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read paginated expenses: {error}")
            raise map_database_error(error)

    def count(
        self,
        *,
        search_term: Optional[str] = None,
        vendor_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        is_draft: Optional[bool] = None,
    ) -> int:
        """
        Count expenses matching the filter criteria.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "SearchTerm": search_term,
                    "VendorId": vendor_id,
                    "StartDate": start_date,
                    "EndDate": end_date,
                    "IsDraft": 1 if is_draft else (0 if is_draft is False else None),
                }
                call_procedure(
                    cursor=cursor,
                    name="CountExpenses",
                    params=params,
                )
                row = cursor.fetchone()
                return row.TotalCount if row else 0
        except Exception as error:
            logger.error(f"Error during count expenses: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[Expense]:
        """
        Delete an expense by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteExpenseById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete expense by ID: {error}")
            raise map_database_error(error)

