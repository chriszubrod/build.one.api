# Python Standard Library Imports
import base64
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports
import pyodbc

# Local Imports
from entities.bill_credit.business.model import BillCredit
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class BillCreditRepository:
    """
    Repository for BillCredit persistence operations.
    """

    def __init__(self):
        """Initialize the BillCreditRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[BillCredit]:
        """
        Convert a database row into a BillCredit dataclass.
        """
        if not row:
            return None

        try:
            return BillCredit(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                vendor_id=getattr(row, "VendorId", None),
                credit_date=getattr(row, "CreditDate", None),
                credit_number=getattr(row, "CreditNumber", None),
                total_amount=Decimal(str(getattr(row, "TotalAmount", None))) if getattr(row, "TotalAmount", None) is not None else None,
                memo=getattr(row, "Memo", None),
                is_draft=bool(getattr(row, "IsDraft", False)) if getattr(row, "IsDraft", None) is not None else None,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during bill credit mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during bill credit mapping: {error}")
            raise map_database_error(error)

    def create(self, *, tenant_id: int = 1, vendor_id: Optional[int] = None, credit_date: Optional[str] = None, credit_number: Optional[str] = None, total_amount: Optional[Decimal] = None, memo: Optional[str] = None, is_draft: bool = True) -> BillCredit:
        """
        Create a new bill credit.
        
        Args:
            tenant_id: Tenant ID for multi-tenant isolation (logged for audit, not yet used for filtering)
            vendor_id: Vendor ID
            credit_date: Credit date
            credit_number: Credit number
            total_amount: Total amount
            memo: Memo
            is_draft: Whether bill credit is in draft state
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateBillCredit",
                    params={
                        "VendorId": vendor_id,
                        "CreditDate": credit_date,
                        "CreditNumber": credit_number,
                        "TotalAmount": float(total_amount) if total_amount is not None else None,
                        "Memo": memo,
                        "IsDraft": 1 if is_draft else 0,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateBillCredit did not return a row.")
                    raise map_database_error(Exception("CreateBillCredit failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create bill credit: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[BillCredit]:
        """
        Read all bill credits.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillCredits",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all bill credits: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[BillCredit]:
        """
        Read a bill credit by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillCreditById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read bill credit by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[BillCredit]:
        """
        Read a bill credit by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillCreditByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read bill credit by public ID: {error}")
            raise map_database_error(error)

    def read_by_credit_number_and_vendor_id(self, credit_number: str, vendor_id: int) -> Optional[BillCredit]:
        """
        Read a bill credit by credit number and vendor ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillCreditByCreditNumberAndVendorId",
                    params={
                        "CreditNumber": credit_number,
                        "VendorId": vendor_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read bill credit by credit number and vendor ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, bill_credit: BillCredit) -> Optional[BillCredit]:
        """
        Update a bill credit by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "Id": bill_credit.id,
                    "RowVersion": bill_credit.row_version_bytes,
                    "VendorId": bill_credit.vendor_id,
                    "CreditDate": bill_credit.credit_date,
                    "CreditNumber": bill_credit.credit_number,
                    "TotalAmount": float(bill_credit.total_amount) if bill_credit.total_amount is not None else None,
                    "Memo": bill_credit.memo,
                }
                # Only include IsDraft if it's explicitly set (not None)
                if bill_credit.is_draft is not None:
                    params["IsDraft"] = 1 if bill_credit.is_draft else 0
                
                call_procedure(
                    cursor=cursor,
                    name="UpdateBillCreditById",
                    params=params,
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update bill credit by ID: {error}")
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
        sort_by: str = "CreditDate",
        sort_direction: str = "DESC",
    ) -> list[BillCredit]:
        """
        Read bill credits with pagination and filtering.
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
                    name="ReadBillCreditsPaginated",
                    params=params,
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read paginated bill credits: {error}")
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
        Count bill credits matching the filter criteria.
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
                    name="CountBillCredits",
                    params=params,
                )
                row = cursor.fetchone()
                return row.TotalCount if row else 0
        except Exception as error:
            logger.error(f"Error during count bill credits: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[BillCredit]:
        """
        Delete a bill credit by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteBillCreditById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete bill credit by ID: {error}")
            raise map_database_error(error)
