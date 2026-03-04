# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.invoice.connector.invoice.business.model import InvoiceInvoice
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class InvoiceInvoiceRepository:
    """
    Repository for InvoiceInvoice persistence operations.
    """

    def __init__(self):
        """Initialize the InvoiceInvoiceRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[InvoiceInvoice]:
        """
        Convert a database row into an InvoiceInvoice dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return InvoiceInvoice(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                invoice_id=getattr(row, "InvoiceId", None),
                qbo_invoice_id=getattr(row, "QboInvoiceId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during invoice invoice mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during invoice invoice mapping: {error}")
            raise map_database_error(error)

    def create(self, *, invoice_id: int, qbo_invoice_id: int) -> InvoiceInvoice:
        """
        Create a new InvoiceInvoice mapping record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateInvoiceInvoice",
                        params={
                            "InvoiceId": invoice_id,
                            "QboInvoiceId": qbo_invoice_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateInvoiceInvoice did not return a row.")
                        raise map_database_error(Exception("CreateInvoiceInvoice failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create invoice invoice: {error}")
            raise map_database_error(error)

    def read_all(self) -> list:
        """
        Read all InvoiceInvoice mappings (for pre-loading into memory).
        Returns a list of InvoiceInvoice objects keyed by qbo_invoice_id.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.execute("SELECT * FROM [qbo].[InvoiceInvoice]")
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read all invoice invoices: {error}")
            raise map_database_error(error)

    def read_all_invoice_ids(self) -> set:
        """
        Read all Invoice IDs that have an InvoiceInvoice mapping.
        Returns a set of invoice_id values for fast lookup.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    cursor.execute("SELECT [InvoiceId] FROM [qbo].[InvoiceInvoice]")
                    rows = cursor.fetchall()
                    return {row.InvoiceId for row in rows if row.InvoiceId}
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read all invoice ids: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[InvoiceInvoice]:
        """
        Read an InvoiceInvoice mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadInvoiceInvoiceById",
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
            logger.error(f"Error during read invoice invoice by ID: {error}")
            raise map_database_error(error)

    def read_by_invoice_id(self, invoice_id: int) -> Optional[InvoiceInvoice]:
        """
        Read an InvoiceInvoice mapping record by Invoice ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadInvoiceInvoiceByInvoiceId",
                        params={"InvoiceId": invoice_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read invoice invoice by invoice ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_invoice_id(self, qbo_invoice_id: int) -> Optional[InvoiceInvoice]:
        """
        Read an InvoiceInvoice mapping record by QboInvoice ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadInvoiceInvoiceByQboInvoiceId",
                        params={"QboInvoiceId": qbo_invoice_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read invoice invoice by QBO invoice ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[InvoiceInvoice]:
        """
        Delete an InvoiceInvoice mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteInvoiceInvoiceById",
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
            logger.error(f"Error during delete invoice invoice by ID: {error}")
            raise map_database_error(error)
