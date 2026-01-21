# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.term.connector.payment_term.business.model import TermPaymentTerm
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class TermPaymentTermRepository:
    """
    Repository for TermPaymentTerm persistence operations.
    """

    def __init__(self):
        """Initialize the TermPaymentTermRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[TermPaymentTerm]:
        """
        Convert a database row into a TermPaymentTerm dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return TermPaymentTerm(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                payment_term_id=getattr(row, "PaymentTermId", None),
                qbo_term_id=getattr(row, "QboTermId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during term payment term mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during term payment term mapping: {error}")
            raise map_database_error(error)

    def create(self, *, payment_term_id: int, qbo_term_id: int) -> TermPaymentTerm:
        """
        Create a new TermPaymentTerm mapping record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateTermPaymentTerm",
                        params={
                            "PaymentTermId": payment_term_id,
                            "QboTermId": qbo_term_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateTermPaymentTerm did not return a row.")
                        raise map_database_error(Exception("CreateTermPaymentTerm failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create term payment term: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[TermPaymentTerm]:
        """
        Read a TermPaymentTerm mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadTermPaymentTermById",
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
            logger.error(f"Error during read term payment term by ID: {error}")
            raise map_database_error(error)

    def read_by_payment_term_id(self, payment_term_id: int) -> Optional[TermPaymentTerm]:
        """
        Read a TermPaymentTerm mapping record by PaymentTerm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadTermPaymentTermByPaymentTermId",
                        params={"PaymentTermId": payment_term_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read term payment term by payment term ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_term_id(self, qbo_term_id: int) -> Optional[TermPaymentTerm]:
        """
        Read a TermPaymentTerm mapping record by QboTerm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadTermPaymentTermByQboTermId",
                        params={"QboTermId": qbo_term_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read term payment term by QBO term ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, term_payment_term: TermPaymentTerm) -> Optional[TermPaymentTerm]:
        """
        Update a TermPaymentTerm mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="UpdateTermPaymentTermById",
                        params={
                            "Id": term_payment_term.id,
                            "RowVersion": term_payment_term.row_version_bytes,
                            "PaymentTermId": term_payment_term.payment_term_id,
                            "QboTermId": term_payment_term.qbo_term_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("UpdateTermPaymentTermById did not return a row.")
                        raise map_database_error(Exception("UpdateTermPaymentTermById failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update term payment term by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[TermPaymentTerm]:
        """
        Delete a TermPaymentTerm mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteTermPaymentTermById",
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
            logger.error(f"Error during delete term payment term by ID: {error}")
            raise map_database_error(error)
