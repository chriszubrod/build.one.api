# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.model import InvoiceLineItemInvoiceLine
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class InvoiceLineItemInvoiceLineRepository:
    """
    Repository for InvoiceLineItemInvoiceLine persistence operations.
    """

    def __init__(self):
        """Initialize the InvoiceLineItemInvoiceLineRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[InvoiceLineItemInvoiceLine]:
        """
        Convert a database row into an InvoiceLineItemInvoiceLine dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return InvoiceLineItemInvoiceLine(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                invoice_line_item_id=getattr(row, "InvoiceLineItemId", None),
                qbo_invoice_line_id=getattr(row, "QboInvoiceLineId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during invoice line item invoice line mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during invoice line item invoice line mapping: {error}")
            raise map_database_error(error)

    def create(self, *, invoice_line_item_id: int, qbo_invoice_line_id: int) -> InvoiceLineItemInvoiceLine:
        """
        Create a new InvoiceLineItemInvoiceLine mapping record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateInvoiceLineItemInvoiceLine",
                        params={
                            "InvoiceLineItemId": invoice_line_item_id,
                            "QboInvoiceLineId": qbo_invoice_line_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateInvoiceLineItemInvoiceLine did not return a row.")
                        raise map_database_error(Exception("CreateInvoiceLineItemInvoiceLine failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create invoice line item invoice line: {error}")
            raise map_database_error(error)

    def read_all(self) -> list:
        """
        Read all InvoiceLineItemInvoiceLine mappings (for pre-loading into memory).
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.execute("SELECT * FROM [qbo].[InvoiceLineItemInvoiceLine]")
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read all invoice line item invoice lines: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[InvoiceLineItemInvoiceLine]:
        """
        Read an InvoiceLineItemInvoiceLine mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadInvoiceLineItemInvoiceLineById",
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
            logger.error(f"Error during read invoice line item invoice line by ID: {error}")
            raise map_database_error(error)

    def read_by_invoice_line_item_id(self, invoice_line_item_id: int) -> Optional[InvoiceLineItemInvoiceLine]:
        """
        Read an InvoiceLineItemInvoiceLine mapping record by InvoiceLineItem ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadInvoiceLineItemInvoiceLineByInvoiceLineItemId",
                        params={"InvoiceLineItemId": invoice_line_item_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read invoice line item invoice line by invoice line item ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_invoice_line_id(self, qbo_invoice_line_id: int) -> Optional[InvoiceLineItemInvoiceLine]:
        """
        Read an InvoiceLineItemInvoiceLine mapping record by QboInvoiceLine ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadInvoiceLineItemInvoiceLineByQboInvoiceLineId",
                        params={"QboInvoiceLineId": qbo_invoice_line_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read invoice line item invoice line by QBO invoice line ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[InvoiceLineItemInvoiceLine]:
        """
        Delete an InvoiceLineItemInvoiceLine mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteInvoiceLineItemInvoiceLineById",
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
            logger.error(f"Error during delete invoice line item invoice line by ID: {error}")
            raise map_database_error(error)
