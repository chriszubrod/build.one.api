# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.payment_term.business.model import PaymentTerm
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class PaymentTermRepository:
    """
    Repository for PaymentTerm persistence operations.
    """

    def __init__(self):
        """Initialize the PaymentTermRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[PaymentTerm]:
        """
        Convert a database row into a PaymentTerm dataclass.
        """
        if not row:
            return None

        try:
            return PaymentTerm(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                name=row.Name,
                description=row.Description,
                discount_percent=row.DiscountPercent,
                discount_days=row.DiscountDays,
                due_days=row.DueDays,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during payment term mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during payment term mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        tenant_id: int = 1,
        name: Optional[str],
        description: Optional[str],
        discount_percent: Optional[float] = None,
        discount_days: Optional[int] = None,
        due_days: Optional[int] = None,
    ) -> PaymentTerm:
        """
        Create a new payment term.
        
        Args:
            tenant_id: Tenant ID for multi-tenant isolation (logged for audit, not yet used for filtering)
            name: Payment term name
            description: Payment term description
            discount_percent: Discount percentage (optional)
            discount_days: Discount days (optional)
            due_days: Due days (optional)
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                # Note: tenant_id is accepted for audit trail purposes
                # Future: Add TenantId param when stored procedure supports it
                call_procedure(
                    cursor=cursor,
                    name="CreatePaymentTerm",
                    params={
                        "Name": name,
                        "Description": description,
                        "DiscountPercent": discount_percent,
                        "DiscountDays": discount_days,
                        "DueDays": due_days,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreatePaymentTerm did not return a row.")
                    raise map_database_error(Exception("CreatePaymentTerm failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create payment term: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[PaymentTerm]:
        """
        Read all payment terms.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadPaymentTerms",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all payment terms: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[PaymentTerm]:
        """
        Read a payment term by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadPaymentTermById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read payment term by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[PaymentTerm]:
        """
        Read a payment term by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadPaymentTermByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read payment term by public ID: {error}")
            raise map_database_error(error)

    def read_by_name(self, name: str) -> Optional[PaymentTerm]:
        """
        Read a payment term by name.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadPaymentTermByName",
                    params={"Name": name},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read payment term by name: {error}")
            raise map_database_error(error)

    def update_by_id(self, payment_term: PaymentTerm) -> Optional[PaymentTerm]:
        """
        Update a payment term by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdatePaymentTermById",
                    params={
                        "Id": payment_term.id,
                        "RowVersion": payment_term.row_version_bytes,
                        "Name": payment_term.name,
                        "Description": payment_term.description,
                        "DiscountPercent": payment_term.discount_percent,
                        "DiscountDays": payment_term.discount_days,
                        "DueDays": payment_term.due_days,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update payment term by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[PaymentTerm]:
        """
        Delete a payment term by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeletePaymentTermById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete payment term by ID: {error}")
            raise map_database_error(error)
