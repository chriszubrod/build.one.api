# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from services.customer.business.model import Customer
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class CustomerRepository:
    """
    Repository for Customer persistence operations.
    """

    def __init__(self):
        """Initialize the CustomerRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Customer]:
        """
        Convert a database row into a Customer dataclass.
        """
        if not row:
            return None

        try:
            return Customer(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                name=row.Name,
                email=row.Email,
                phone=row.Phone,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during customer mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during customer mapping: {error}")
            raise map_database_error(error)

    def create(self, *, tenant_id: int = 1, name: str, email: str, phone: str) -> Customer:
        """
        Create a new customer.
        
        Args:
            tenant_id: Tenant ID for multi-tenant isolation (logged for audit, not yet used for filtering)
            name: Customer name
            email: Customer email
            phone: Customer phone
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                # Note: tenant_id is accepted for audit trail purposes
                # Future: Add TenantId param when stored procedure supports it
                call_procedure(
                    cursor=cursor,
                    name="CreateCustomer",
                    params={
                        "Name": name,
                        "Email": email,
                        "Phone": phone,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateCustomer did not return a row.")
                    raise map_database_error(Exception("CreateCustomer failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create customer: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[Customer]:
        """
        Read all customers.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCustomers",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all customers: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[Customer]:
        """
        Read a customer by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCustomerById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read customer by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Customer]:
        """
        Read a customer by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCustomerByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read customer by public ID: {error}")
            raise map_database_error(error)

    def read_by_name(self, name: str) -> Optional[Customer]:
        """
        Read a customer by name.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCustomerByName",
                    params={"Name": name},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read customer by name: {error}")
            raise map_database_error(error)

    def update_by_id(self, customer: Customer) -> Optional[Customer]:
        """
        Update a customer by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateCustomerById",
                    params={
                        "Id": customer.id,
                        "RowVersion": customer.row_version_bytes,
                        "Name": customer.name,
                        "Email": customer.email,
                        "Phone": customer.phone,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update customer by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[Customer]:
        """
        Delete a customer by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteCustomerById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete customer by ID: {error}")
            raise map_database_error(error)
