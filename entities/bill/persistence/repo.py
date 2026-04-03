# Python Standard Library Imports
import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from decimal import Decimal

# Third-party Imports
import pyodbc

# Local Imports
from entities.bill.business.model import Bill
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class BillRepository:
    """
    Repository for Bill persistence operations.
    """

    def __init__(self):
        """Initialize the BillRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Bill]:
        """
        Convert a database row into a Bill dataclass.
        """
        if not row:
            return None

        try:
            return Bill(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                vendor_id=getattr(row, "VendorId", None),
                payment_term_id=getattr(row, "PaymentTermId", None),
                bill_date=getattr(row, "BillDate", None),
                due_date=getattr(row, "DueDate", None),
                bill_number=getattr(row, "BillNumber", None),
                total_amount=Decimal(str(getattr(row, "TotalAmount", None))) if getattr(row, "TotalAmount", None) is not None else None,
                memo=getattr(row, "Memo", None),
                is_draft=bool(getattr(row, "IsDraft", False)) if getattr(row, "IsDraft", None) is not None else None,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during bill mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during bill mapping: {error}")
            raise map_database_error(error)

    def create(self, *, tenant_id: int = 1, vendor_id: Optional[int] = None, payment_term_id: Optional[int] = None, bill_date: Optional[str] = None, due_date: Optional[str] = None, bill_number: Optional[str] = None, total_amount: Optional[Decimal] = None, memo: Optional[str] = None, is_draft: bool = True) -> Bill:
        """
        Create a new bill.
        
        Args:
            tenant_id: Tenant ID for multi-tenant isolation (logged for audit, not yet used for filtering)
            vendor_id: Vendor ID
            payment_term_id: Payment term ID
            bill_date: Bill date
            due_date: Due date
            bill_number: Bill number
            total_amount: Total amount
            memo: Memo
            is_draft: Whether bill is in draft state
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                # Note: tenant_id is accepted for audit trail purposes
                # Future: Add TenantId param when stored procedure supports it
                call_procedure(
                    cursor=cursor,
                    name="CreateBill",
                    params={
                        "VendorId": vendor_id,
                        "PaymentTermId": payment_term_id,
                        "BillDate": bill_date,
                        "DueDate": due_date,
                        "BillNumber": bill_number,
                        "TotalAmount": float(total_amount) if total_amount is not None else None,
                        "Memo": memo,
                        "IsDraft": 1 if is_draft else 0,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateBill did not return a row.")
                    raise map_database_error(Exception("CreateBill failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create bill: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[Bill]:
        """
        Read all bills.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBills",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all bills: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[Bill]:
        """
        Read a bill by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read bill by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Bill]:
        """
        Read a bill by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read bill by public ID: {error}")
            raise map_database_error(error)

    def read_by_bill_number(self, bill_number: str) -> Optional[Bill]:
        """
        Read a bill by bill number.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillByBillNumber",
                    params={"BillNumber": bill_number},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read bill by bill number: {error}")
            raise map_database_error(error)

    def read_by_bill_number_and_vendor_id(self, bill_number: str, vendor_id: int, bill_date: str = None) -> Optional[Bill]:
        """
        Read a bill by bill number, vendor ID, and optionally bill date.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillByBillNumberAndVendorId",
                    params={
                        "BillNumber": bill_number,
                        "VendorId": vendor_id,
                        "BillDate": bill_date,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read bill by bill number and vendor ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, bill: Bill) -> Optional[Bill]:
        """
        Update a bill by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "Id": bill.id,
                    "RowVersion": bill.row_version_bytes,
                    "VendorId": bill.vendor_id,
                    "PaymentTermId": bill.payment_term_id,
                    "BillDate": bill.bill_date,
                    "DueDate": bill.due_date,
                    "BillNumber": bill.bill_number,
                    "TotalAmount": float(bill.total_amount) if bill.total_amount is not None else None,
                    "Memo": bill.memo,
                }
                # Only include IsDraft if it's explicitly set (not None)
                if bill.is_draft is not None:
                    params["IsDraft"] = 1 if bill.is_draft else 0
                
                call_procedure(
                    cursor=cursor,
                    name="UpdateBillById",
                    params=params,
                )
                row = cursor.fetchone()
                if not row:
                    logger.warning(
                        "UpdateBillById returned no row (id=%s); possible row-version conflict or record not found.",
                        bill.id,
                    )
                    raise map_database_error(
                        Exception(
                            "Update did not match any row; the bill may have been modified by another process (row-version conflict) or no longer exists."
                        )
                    )
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update bill by ID: {error}")
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
        sort_by: str = "BillDate",
        sort_direction: str = "DESC",
    ) -> list[Bill]:
        """
        Read bills with pagination and filtering.
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
                    name="ReadBillsPaginated",
                    params=params,
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read paginated bills: {error}")
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
        Count bills matching the filter criteria.
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
                    name="CountBills",
                    params=params,
                )
                row = cursor.fetchone()
                return row.TotalCount if row else 0
        except Exception as error:
            logger.error(f"Error during count bills: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[Bill]:
        """
        Delete a bill by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteBillById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete bill by ID: {error}")
            raise map_database_error(error)

    def set_completion_result(self, public_id: str, result: dict[str, Any]) -> None:
        """Store completion result for a bill (permanent record)."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpsertBillCompletionResult",
                    params={
                        "BillPublicId": public_id,
                        "ResultJson": json.dumps(result, default=str),
                    },
                )
        except Exception as error:
            logger.error(f"Error storing bill completion result: {error}")
            raise map_database_error(error)

    def get_completion_result(self, public_id: str) -> Optional[dict[str, Any]]:
        """Return completion result for a bill."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="GetBillCompletionResult",
                    params={"BillPublicId": public_id},
                )
                row = cursor.fetchone()
                if not row or not getattr(row, "ResultJson", None):
                    return None
                return json.loads(row.ResultJson)
        except Exception as error:
            logger.error(f"Error reading bill completion result: {error}")
            raise map_database_error(error)
