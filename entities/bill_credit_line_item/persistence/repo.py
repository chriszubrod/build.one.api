# Python Standard Library Imports
import base64
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports
import pyodbc

# Local Imports
from entities.bill_credit_line_item.business.model import BillCreditLineItem
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class BillCreditLineItemRepository:
    """
    Repository for BillCreditLineItem persistence operations.
    """

    def __init__(self):
        """Initialize the BillCreditLineItemRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[BillCreditLineItem]:
        """
        Convert a database row into a BillCreditLineItem dataclass.
        """
        if not row:
            return None

        try:
            return BillCreditLineItem(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                bill_credit_id=getattr(row, "BillCreditId", None),
                sub_cost_code_id=getattr(row, "SubCostCodeId", None),
                project_id=getattr(row, "ProjectId", None),
                description=getattr(row, "Description", None),
                quantity=Decimal(str(getattr(row, "Quantity", None))) if getattr(row, "Quantity", None) is not None else None,
                unit_price=Decimal(str(getattr(row, "UnitPrice", None))) if getattr(row, "UnitPrice", None) is not None else None,
                amount=Decimal(str(getattr(row, "Amount", None))) if getattr(row, "Amount", None) is not None else None,
                is_billable=bool(getattr(row, "IsBillable", False)) if getattr(row, "IsBillable", None) is not None else None,
                is_billed=bool(getattr(row, "IsBilled", False)) if getattr(row, "IsBilled", None) is not None else None,
                billable_amount=Decimal(str(getattr(row, "BillableAmount", None))) if getattr(row, "BillableAmount", None) is not None else None,
                is_draft=bool(getattr(row, "IsDraft", False)) if getattr(row, "IsDraft", None) is not None else None,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during bill credit line item mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during bill credit line item mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        bill_credit_id: int,
        sub_cost_code_id: Optional[int] = None,
        project_id: Optional[int] = None,
        description: Optional[str] = None,
        quantity: Optional[Decimal] = None,
        unit_price: Optional[Decimal] = None,
        amount: Optional[Decimal] = None,
        is_billable: Optional[bool] = None,
        is_billed: Optional[bool] = None,
        billable_amount: Optional[Decimal] = None,
        is_draft: bool = True,
    ) -> BillCreditLineItem:
        """
        Create a new bill credit line item.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateBillCreditLineItem",
                    params={
                        "BillCreditId": bill_credit_id,
                        "SubCostCodeId": sub_cost_code_id,
                        "ProjectId": project_id,
                        "Description": description,
                        "Quantity": Decimal(str(quantity)) if quantity is not None else None,
                        "UnitPrice": Decimal(str(unit_price)) if unit_price is not None else None,
                        "Amount": Decimal(str(amount)) if amount is not None else None,
                        "IsBillable": 1 if is_billable else (0 if is_billable is False else None),
                        "IsBilled": 1 if is_billed else (0 if is_billed is False else None),
                        "BillableAmount": Decimal(str(billable_amount)) if billable_amount is not None else None,
                        "IsDraft": 1 if is_draft else 0,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateBillCreditLineItem did not return a row.")
                    raise map_database_error(Exception("CreateBillCreditLineItem failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create bill credit line item: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[BillCreditLineItem]:
        """
        Read all bill credit line items.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillCreditLineItems",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all bill credit line items: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[BillCreditLineItem]:
        """
        Read a bill credit line item by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillCreditLineItemById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read bill credit line item by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[BillCreditLineItem]:
        """
        Read a bill credit line item by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillCreditLineItemByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read bill credit line item by public ID: {error}")
            raise map_database_error(error)

    def read_by_bill_credit_id(self, bill_credit_id: int) -> list[BillCreditLineItem]:
        """
        Read all bill credit line items for a specific bill credit.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadBillCreditLineItemsByBillCreditId",
                    params={"BillCreditId": bill_credit_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read bill credit line items by bill credit ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, line_item: BillCreditLineItem) -> Optional[BillCreditLineItem]:
        """
        Update a bill credit line item by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "Id": line_item.id,
                    "RowVersion": line_item.row_version_bytes,
                    "BillCreditId": line_item.bill_credit_id,
                    "SubCostCodeId": line_item.sub_cost_code_id,
                    "ProjectId": line_item.project_id,
                    "Description": line_item.description,
                    "Quantity": Decimal(str(line_item.quantity)) if line_item.quantity is not None else None,
                    "UnitPrice": Decimal(str(line_item.unit_price)) if line_item.unit_price is not None else None,
                    "Amount": Decimal(str(line_item.amount)) if line_item.amount is not None else None,
                    "IsBillable": 1 if line_item.is_billable else (0 if line_item.is_billable is False else None),
                    "IsBilled": 1 if line_item.is_billed else (0 if line_item.is_billed is False else None),
                    "BillableAmount": Decimal(str(line_item.billable_amount)) if line_item.billable_amount is not None else None,
                }
                # Only include IsDraft if it's explicitly set (not None)
                if line_item.is_draft is not None:
                    params["IsDraft"] = 1 if line_item.is_draft else 0
                
                call_procedure(
                    cursor=cursor,
                    name="UpdateBillCreditLineItemById",
                    params=params,
                )
                row = cursor.fetchone()
                if not row:
                    logger.warning(
                        "UpdateBillCreditLineItemById returned no row (id=%s); possible row-version conflict or record not found.",
                        line_item.id,
                    )
                    raise map_database_error(
                        Exception(
                            "Update did not match any row; the bill credit line item may have been modified by another process (row-version conflict) or no longer exists."
                        )
                    )
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update bill credit line item by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[BillCreditLineItem]:
        """
        Delete a bill credit line item by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteBillCreditLineItemById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete bill credit line item by ID: {error}")
            raise map_database_error(error)
