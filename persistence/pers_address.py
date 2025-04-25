"""
Module for address persistence.
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
class Address:
    """Represents an address in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    street_one: Optional[str] = None
    street_two: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    transaction_id: Optional[int] = None


    @classmethod
    def from_db_row(cls, row) -> Optional['Address']:
        """Creates an Address instance from a database row."""
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            street_one=getattr(row, 'StreetOne', None),
            street_two=getattr(row, 'StreetTwo', None),
            city=getattr(row, 'City', None),
            state=getattr(row, 'State', None),
            zip_code=getattr(row, 'ZipCode', None),
            transaction_id=getattr(row, 'TransactionId', None)
        )


def create_address(address: Address) -> PersistenceResponse:
    """
    Creates a new address record in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateAddress(?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    address.created_datetime,
                    address.modified_datetime,
                    address.street_one,
                    address.street_two,
                    address.city,
                    address.state,
                    address.zip_code
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Address created successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Address creation failed",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Error in create address: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_addresses() -> PersistenceResponse:
    """
    Retrieves all addresses from the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadAddresses()}"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[Address.from_db_row(row) for row in rows],
                        message="Addresses retrieved successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No addresses found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Error in read addresses: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_address_by_id(address_id: int) -> PersistenceResponse:
    """
    Retrieves an address from the database by ID.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadAddressById(?)}"
                row = cursor.execute(sql, address_id).fetchone()
                if row:
                    return PersistenceResponse(
                        data=Address.from_db_row(row),
                        message="Address found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Address not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Error in read address by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_address_by_guid(address_guid: str) -> PersistenceResponse:
    """
    Retrieves an address from the database by GUID.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadAddressByGuid(?)}"
                row = cursor.execute(sql, address_guid).fetchone()
                if row:
                    return PersistenceResponse(
                        data=Address.from_db_row(row),
                        message="Address found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Address not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Error in read address by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_address_by_id(address: Address) -> PersistenceResponse:
    """
    Updates an address in the database by ID.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateAddressById(?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    address.id,
                    address.modified_datetime,
                    address.street_one,
                    address.street_two,
                    address.city,
                    address.state,
                    address.zip_code
                ).rowcount
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Address updated successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Address update failed",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Error in update address by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def delete_address_by_id(address_id: int) -> PersistenceResponse:
    """
    Deletes an address from the database by ID.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL DeleteAddressById(?)}"
                rowcount = cursor.execute(sql, address_id).rowcount
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Address deleted successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Address deletion failed",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Error in delete address by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
