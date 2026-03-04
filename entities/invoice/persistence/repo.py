# Python Standard Library Imports
import base64
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports
import pyodbc

# Local Imports
from entities.invoice.business.model import Invoice
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class InvoiceRepository:
    """
    Repository for Invoice persistence operations.
    """

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Invoice]:
        if not row:
            return None

        try:
            return Invoice(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                project_id=getattr(row, "ProjectId", None),
                payment_term_id=getattr(row, "PaymentTermId", None),
                invoice_date=getattr(row, "InvoiceDate", None),
                due_date=getattr(row, "DueDate", None),
                invoice_number=getattr(row, "InvoiceNumber", None),
                total_amount=Decimal(str(getattr(row, "TotalAmount", None))) if getattr(row, "TotalAmount", None) is not None else None,
                memo=getattr(row, "Memo", None),
                is_draft=bool(getattr(row, "IsDraft", False)) if getattr(row, "IsDraft", None) is not None else None,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during invoice mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during invoice mapping: {error}")
            raise map_database_error(error)

    def create(self, *, tenant_id: int = 1, project_id: Optional[int] = None, payment_term_id: Optional[int] = None, invoice_date: Optional[str] = None, due_date: Optional[str] = None, invoice_number: Optional[str] = None, total_amount: Optional[Decimal] = None, memo: Optional[str] = None, is_draft: bool = True) -> Invoice:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateInvoice",
                    params={
                        "ProjectId": project_id,
                        "PaymentTermId": payment_term_id,
                        "InvoiceDate": invoice_date,
                        "DueDate": due_date,
                        "InvoiceNumber": invoice_number,
                        "TotalAmount": float(total_amount) if total_amount is not None else None,
                        "Memo": memo,
                        "IsDraft": 1 if is_draft else 0,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateInvoice did not return a row.")
                    raise map_database_error(Exception("CreateInvoice failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create invoice: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[Invoice]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadInvoices", params={})
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all invoices: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[Invoice]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadInvoiceById", params={"Id": id})
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read invoice by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Invoice]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadInvoiceByPublicId", params={"PublicId": public_id})
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read invoice by public ID: {error}")
            raise map_database_error(error)

    def read_by_invoice_number(self, invoice_number: str) -> Optional[Invoice]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadInvoiceByInvoiceNumber", params={"InvoiceNumber": invoice_number})
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read invoice by invoice number: {error}")
            raise map_database_error(error)

    def read_by_invoice_number_and_project_id(self, invoice_number: str, project_id: int) -> Optional[Invoice]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadInvoiceByInvoiceNumberAndProjectId",
                    params={"InvoiceNumber": invoice_number, "ProjectId": project_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read invoice by invoice number and project ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, invoice: Invoice) -> Optional[Invoice]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "Id": invoice.id,
                    "RowVersion": invoice.row_version_bytes,
                    "ProjectId": invoice.project_id,
                    "PaymentTermId": invoice.payment_term_id,
                    "InvoiceDate": invoice.invoice_date,
                    "DueDate": invoice.due_date,
                    "InvoiceNumber": invoice.invoice_number,
                    "TotalAmount": float(invoice.total_amount) if invoice.total_amount is not None else None,
                    "Memo": invoice.memo,
                }
                if invoice.is_draft is not None:
                    params["IsDraft"] = 1 if invoice.is_draft else 0

                call_procedure(cursor=cursor, name="UpdateInvoiceById", params=params)
                row = cursor.fetchone()
                if not row:
                    logger.warning(
                        "UpdateInvoiceById returned no row (id=%s); possible row-version conflict.",
                        invoice.id,
                    )
                    raise map_database_error(
                        Exception("Update did not match any row; the invoice may have been modified by another process.")
                    )
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update invoice by ID: {error}")
            raise map_database_error(error)

    def read_paginated(
        self,
        *,
        page_number: int = 1,
        page_size: int = 50,
        search_term: Optional[str] = None,
        project_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        is_draft: Optional[bool] = None,
        sort_by: str = "InvoiceDate",
        sort_direction: str = "DESC",
    ) -> list[Invoice]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "PageNumber": page_number,
                    "PageSize": page_size,
                    "SearchTerm": search_term,
                    "ProjectId": project_id,
                    "StartDate": start_date,
                    "EndDate": end_date,
                    "IsDraft": 1 if is_draft else (0 if is_draft is False else None),
                    "SortBy": sort_by,
                    "SortDirection": sort_direction,
                }
                call_procedure(cursor=cursor, name="ReadInvoicesPaginated", params=params)
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read paginated invoices: {error}")
            raise map_database_error(error)

    def count(
        self,
        *,
        search_term: Optional[str] = None,
        project_id: Optional[int] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        is_draft: Optional[bool] = None,
    ) -> int:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "SearchTerm": search_term,
                    "ProjectId": project_id,
                    "StartDate": start_date,
                    "EndDate": end_date,
                    "IsDraft": 1 if is_draft else (0 if is_draft is False else None),
                }
                call_procedure(cursor=cursor, name="CountInvoices", params=params)
                row = cursor.fetchone()
                return row.TotalCount if row else 0
        except Exception as error:
            logger.error(f"Error during count invoices: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[Invoice]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="DeleteInvoiceById", params={"Id": id})
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete invoice by ID: {error}")
            raise map_database_error(error)
