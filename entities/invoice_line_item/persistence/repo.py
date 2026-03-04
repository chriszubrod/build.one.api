# Python Standard Library Imports
import base64
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports
import pyodbc

# Local Imports
from entities.invoice_line_item.business.model import InvoiceLineItem
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class InvoiceLineItemRepository:
    """
    Repository for InvoiceLineItem persistence operations.
    """

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[InvoiceLineItem]:
        if not row:
            return None

        try:
            return InvoiceLineItem(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                invoice_id=getattr(row, "InvoiceId", None),
                source_type=getattr(row, "SourceType", None),
                bill_line_item_id=getattr(row, "BillLineItemId", None),
                expense_line_item_id=getattr(row, "ExpenseLineItemId", None),
                bill_credit_line_item_id=getattr(row, "BillCreditLineItemId", None),
                description=getattr(row, "Description", None),
                amount=Decimal(str(getattr(row, "Amount", None))) if getattr(row, "Amount", None) is not None else None,
                markup=Decimal(str(getattr(row, "Markup", None))) if getattr(row, "Markup", None) is not None else None,
                price=Decimal(str(getattr(row, "Price", None))) if getattr(row, "Price", None) is not None else None,
                is_draft=bool(getattr(row, "IsDraft", False)) if getattr(row, "IsDraft", None) is not None else None,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during invoice line item mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during invoice line item mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        invoice_id: int,
        source_type: str,
        bill_line_item_id: Optional[int] = None,
        expense_line_item_id: Optional[int] = None,
        bill_credit_line_item_id: Optional[int] = None,
        description: Optional[str] = None,
        amount: Optional[Decimal] = None,
        markup: Optional[Decimal] = None,
        price: Optional[Decimal] = None,
        is_draft: bool = True,
    ) -> InvoiceLineItem:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateInvoiceLineItem",
                    params={
                        "InvoiceId": invoice_id,
                        "SourceType": source_type,
                        "BillLineItemId": bill_line_item_id,
                        "ExpenseLineItemId": expense_line_item_id,
                        "BillCreditLineItemId": bill_credit_line_item_id,
                        "Description": description,
                        "Amount": float(amount) if amount is not None else None,
                        "Markup": float(markup) if markup is not None else None,
                        "Price": float(price) if price is not None else None,
                        "IsDraft": 1 if is_draft else 0,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateInvoiceLineItem did not return a row.")
                    raise map_database_error(Exception("CreateInvoiceLineItem failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create invoice line item: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[InvoiceLineItem]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadInvoiceLineItems", params={})
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all invoice line items: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[InvoiceLineItem]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadInvoiceLineItemById", params={"Id": id})
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read invoice line item by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[InvoiceLineItem]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadInvoiceLineItemByPublicId", params={"PublicId": public_id})
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read invoice line item by public ID: {error}")
            raise map_database_error(error)

    def read_by_invoice_id(self, invoice_id: int) -> list[InvoiceLineItem]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadInvoiceLineItemsByInvoiceId", params={"InvoiceId": invoice_id})
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read invoice line items by invoice ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, line_item: InvoiceLineItem) -> Optional[InvoiceLineItem]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "Id": line_item.id,
                    "RowVersion": line_item.row_version_bytes,
                    "InvoiceId": line_item.invoice_id,
                    "SourceType": line_item.source_type,
                    "BillLineItemId": line_item.bill_line_item_id,
                    "ExpenseLineItemId": line_item.expense_line_item_id,
                    "BillCreditLineItemId": line_item.bill_credit_line_item_id,
                    "Description": line_item.description,
                    "Amount": float(line_item.amount) if line_item.amount is not None else None,
                    "Markup": float(line_item.markup) if line_item.markup is not None else None,
                    "Price": float(line_item.price) if line_item.price is not None else None,
                }
                if line_item.is_draft is not None:
                    params["IsDraft"] = 1 if line_item.is_draft else 0

                call_procedure(cursor=cursor, name="UpdateInvoiceLineItemById", params=params)
                row = cursor.fetchone()
                if not row:
                    logger.warning("UpdateInvoiceLineItemById returned no row (id=%s).", line_item.id)
                    raise map_database_error(Exception("Update did not match any row; possible row-version conflict."))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update invoice line item by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[InvoiceLineItem]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="DeleteInvoiceLineItemById", params={"Id": id})
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete invoice line item by ID: {error}")
            raise map_database_error(error)
