"""
Address persistence service layer.

This module contains all database operations for address entities. It provides
CRUD operations and handles database connections, transactions, and error
handling for the address persistence layer.

Functions:
    create_address: Creates a new address record
    read_address: Retrieves an address record by ID or GUID
    read_addresses: Retrieves all address records
    update_address: Updates an address record by ID or GUID
    delete_address: Deletes an address record by ID or GUID
"""

# Standard Library Imports
from datetime import datetime

# Third-party Imports
import pyodbc

# Local Imports
from modules.address.persistence.models import Address
from shared.database import get_db_connection
from shared.response import PersistenceResponse





def create_address(address: Address) -> PersistenceResponse:
    """
    Creates a new address record in the database.

    Args:
        address: Address instance containing the address data to create
        
    Returns:
        PersistenceResponse with success status and message
        
    Raises:
        pyodbc.Error: If database operation fails
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateAddress(?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
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
        except pyodbc.Error as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Error in create address: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_address(address: Address) -> PersistenceResponse:
    """
    Retrieves a specific address record by its ID or GUID.

    Args:
        address: Address instance with ID or GUID
        
    Returns:
        PersistenceResponse containing Address object or None if not found
        
    Raises:
        pyodbc.Error: If database operation fails
    """
    if not address.id and not address.guid:
        return PersistenceResponse(
            data=None,
            message="Address must have either ID or GUID for read",
            status_code=400,
            success=False,
            timestamp=datetime.now()
        )

    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                if address.id:
                    sql = "{CALL ReadAddressById(?)}"
                    params = (address.id,)
                else:
                    sql = "{CALL ReadAddressByGuid(?)}"
                    params = (address.guid,)

                row = cursor.execute(sql, *params).fetchone()
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
        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Error in read address: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_addresses() -> PersistenceResponse:
    """
    Retrieves all address records from the database.

    Returns:
        PersistenceResponse containing list of Address objects or empty list
        
    Raises:
        pyodbc.Error: If database operation fails
    """
    with get_db_connection() as cnxn:
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
                        data=[],
                        message="No addresses found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except pyodbc.Error as e:
            return PersistenceResponse(
                data=[],
                message=f"Error in read addresses: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_address(address: Address) -> PersistenceResponse:
    """
    Updates an existing address record by ID or GUID.

    Args:
        address: Address instance with updated data (must include valid ID or GUID)
        
    Returns:
        PersistenceResponse with success status and message
        
    Raises:
        pyodbc.Error: If database operation fails
    """
    if not address.id and not address.guid:
        return PersistenceResponse(
            data=None,
            message="Address must have either ID or GUID for update",
            status_code=400,
            success=False,
            timestamp=datetime.now()
        )

    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                if address.id:
                    sql = "{CALL UpdateAddressById(?, ?, ?, ?, ?, ?)}"
                    params = (address.id, address.street_one, address.street_two, address.city, address.state, address.zip_code)

                else:
                    sql = "{CALL UpdateAddressByGuid(?, ?, ?, ?, ?, ?)}"
                    params = (address.guid, address.street_one, address.street_two, address.city, address.state, address.zip_code)

                rowcount = cursor.execute(sql,*params).rowcount
                cnxn.commit()

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
        except pyodbc.Error as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Error in update address: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def delete_address(address: Address) -> PersistenceResponse:
    """
    Deletes an address record by its ID or GUID.

    Args:
        address: Address instance with ID or GUID
        
    Returns:
        PersistenceResponse with success status and message
        
    Raises:
        pyodbc.Error: If database operation fails
    """
    if not address.id and not address.guid:
        return PersistenceResponse(
            data=None,
            message="Address must have either ID or GUID for delete",
            status_code=400,
            success=False,
            timestamp=datetime.now()
        )

    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                if address.id:
                    sql = "{CALL DeleteAddressById(?)}"
                    params = (address.id,)
                else:
                    sql = "{CALL DeleteAddressByGuid(?)}"
                    params = (address.guid,)

                rowcount = cursor.execute(sql, *params).rowcount
                cnxn.commit()

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
        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Error in delete address: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
