# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.base.identity_consistency import IdentityCheckResult
from integrations.intuit.qbo.customer.connector.customer.business.model import CustomerCustomer
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class CustomerCustomerRepository:
    """
    Repository for CustomerCustomer persistence operations.
    """

    def __init__(self):
        """Initialize the CustomerCustomerRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[CustomerCustomer]:
        """
        Convert a database row into a CustomerCustomer dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return CustomerCustomer(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                customer_id=getattr(row, "CustomerId", None),
                qbo_customer_id=getattr(row, "QboCustomerId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during customer customer mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during customer customer mapping: {error}")
            raise map_database_error(error)

    def create(self, *, customer_id: int, qbo_customer_id: int) -> CustomerCustomer:
        """
        Create a new CustomerCustomer mapping record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateCustomerCustomer",
                        params={
                            "CustomerId": customer_id,
                            "QboCustomerId": qbo_customer_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateCustomerCustomer did not return a row.")
                        raise map_database_error(Exception("CreateCustomerCustomer failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create customer customer: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[CustomerCustomer]:
        """
        Read a CustomerCustomer mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadCustomerCustomerById",
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
            logger.error(f"Error during read customer customer by ID: {error}")
            raise map_database_error(error)

    def read_by_customer_id(self, customer_id: int) -> Optional[CustomerCustomer]:
        """
        Read a CustomerCustomer mapping record by Customer ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadCustomerCustomerByCustomerId",
                        params={"CustomerId": customer_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read customer customer by customer ID: {error}")
            raise map_database_error(error)

    def read_identity_check(self, *, local_id: int, qbo_id: str) -> IdentityCheckResult:
        """
        Single JOIN'd read backing `verify_customer_qbo_identity` (U-306):
        replaces the old `read_by_customer_id` + `qbo_customer_repo.read_by_id`
        2-read path with one round trip, and additionally resolves whether
        the mapping table already binds `qbo_id` to a DIFFERENT CustomerId
        (the reverse check that closes U-297's H1).
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadCustomerCustomerIdentityCheckByCustomerId",
                        params={"CustomerId": local_id, "QboId": qbo_id},
                    )
                    row = cursor.fetchone()
                    return IdentityCheckResult(
                        mapping_id=getattr(row, "MappingId", None),
                        forward_external_qbo_id=getattr(row, "ForwardExternalQboId", None),
                        reverse_mapped_local_id=getattr(row, "ReverseMappedLocalId", None),
                    )
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read customer customer identity check: {error}")
            raise map_database_error(error)

    def read_by_qbo_customer_id(self, qbo_customer_id: int) -> Optional[CustomerCustomer]:
        """
        Read a CustomerCustomer mapping record by QboCustomer ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadCustomerCustomerByQboCustomerId",
                        params={"QboCustomerId": qbo_customer_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read customer customer by QBO customer ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, customer_customer: CustomerCustomer) -> Optional[CustomerCustomer]:
        """
        Update a CustomerCustomer mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="UpdateCustomerCustomerById",
                        params={
                            "Id": customer_customer.id,
                            "RowVersion": customer_customer.row_version_bytes,
                            "CustomerId": customer_customer.customer_id,
                            "QboCustomerId": customer_customer.qbo_customer_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("UpdateCustomerCustomerById did not return a row.")
                        raise map_database_error(Exception("UpdateCustomerCustomerById failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update customer customer by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[CustomerCustomer]:
        """
        Delete a CustomerCustomer mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteCustomerCustomerById",
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
            logger.error(f"Error during delete customer customer by ID: {error}")
            raise map_database_error(error)
