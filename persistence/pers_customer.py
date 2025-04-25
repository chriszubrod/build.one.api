"""
Module for customer.
"""

# python standard library imports
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# third party imports
import pyodbc

# local imports
from persistence import pers_database
from persistence.pers_response import PersistenceResponse


@dataclass
class Customer:
    """Represents a customer in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    name: Optional[str] = None
    is_active: Optional[bool] = None
    address_id: Optional[int] = None
    transaction_id: Optional[int] = None


    @classmethod
    def from_db_row(cls, row) -> Optional['Customer']:
        """Creates a Customer instance from a database row."""
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            name=getattr(row, 'Name', None),
            is_active=getattr(row, 'IsActive', None),
            address_id=getattr(row, 'AddressId', None),
            transaction_id=getattr(row, 'TransactionId', None),
        )


def create_customer(customer: Customer) -> PersistenceResponse:
    """Creates a new customer in the database."""
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateCustomer(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    customer.created_datetime,
                    customer.modified_datetime,
                    customer.name,
                    customer.is_active
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Customer created",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Customer not created",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create customer: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_customers() -> PersistenceResponse:
    """Retrieves all customers from the database."""
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadCustomers}"
                rows = cursor.execute(sql).fetchall()
                if rows:  
                    return PersistenceResponse(
                        data=[Customer.from_db_row(row) for row in rows],
                        message="Customers found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No customers found",
                        status_code=501,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read customers: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_customer_by_id(customer_id: int) -> PersistenceResponse:
    """
    Retrieves a customer from the database by ID.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadCustomerById(?)}"
                row = cursor.execute(sql, (customer_id,)).fetchone()
                if row:
                    return PersistenceResponse(
                        data=Customer.from_db_row(row),
                        message="Customer found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Customer by id not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read customer by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_customer_by_guid(customer_guid: str) -> PersistenceResponse:
    """
    Retrieves a customer from the database by GUID.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadCustomerByGUID(?)}"
                row = cursor.execute(sql, (customer_guid,)).fetchone()
                if row:
                    return PersistenceResponse(
                        data=Customer.from_db_row(row),
                        message="Customer found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Customer by guid not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read customer by guid: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_customer_by_name(name: str) -> PersistenceResponse:
    """
    Retrieves a customer from the database by name.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadCustomerByName(?)}"
                row = cursor.execute(sql, name).fetchone()
                if row:
                    return PersistenceResponse(
                        data=Customer.from_db_row(row),
                        message="Customer found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Customer by name not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read customer by name: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_customer_by_id(customer: Customer) -> PersistenceResponse:
    """
    Updates an existing customer in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateCustomerById(?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    customer.id,
                    customer.guid,
                    customer.created_datetime,
                    customer.modified_datetime,
                    customer.name,
                    customer.is_active,
                    customer.address_id,
                    customer.transaction_id
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Customer updated",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Customer by id not updated",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to update customer by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
