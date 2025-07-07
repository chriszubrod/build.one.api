"""
Module for vendor type.
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
class VendorType:
    """Represents a vendor type in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    name: Optional[str] = None
    transaction_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> Optional['VendorType']:
        """Creates a VendorType object from a database row."""
        return cls(
            id=getattr(row, 'Id'),
            guid=getattr(row, 'GUID'),
            created_datetime=getattr(row, 'CreatedDatetime'),
            modified_datetime=getattr(row, 'ModifiedDatetime'),
            name=getattr(row, 'Name'),
            transaction_id=getattr(row, 'TransactionId')
        )


def create_vendor_type(vendor_type: VendorType) -> PersistenceResponse:
    """Creates a vendor type in the database."""
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateVendorType (?)}"
                rowcount = cursor.execute(
                    sql,
                    vendor_type.name
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Vendor type created",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Vendor type not created",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error, pyodbc.IntegrityError) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create vendor type: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_vendor_types() -> PersistenceResponse:
    """
    Retrieves all vendor types from the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadVendorTypes}"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[VendorType.from_db_row(row) for row in rows],
                        message="Vendor types found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No vendor types found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error, pyodbc.IntegrityError) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read vendor types: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_vendor_type_by_name(vendor_type_name: str) -> PersistenceResponse:
    """
    Retrieves a vendor type by name from the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadVendorTypeByName (?)}"
                row = cursor.execute(sql, vendor_type_name).fetchone()
                if row:
                    return PersistenceResponse(
                        data=VendorType.from_db_row(row),
                        message="Vendor type found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Vendor type not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error, pyodbc.IntegrityError) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read vendor type by name: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_vendor_type_by_id(vendor_type_id: int) -> PersistenceResponse:
    """
    Retrieves a vendor type by id from the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadVendorTypeByID (?)}"
                row = cursor.execute(sql, vendor_type_id).fetchone()
                if row:
                    return PersistenceResponse(
                        data=VendorType.from_db_row(row),
                        message="Vendor type found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Vendor type not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error, pyodbc.IntegrityError) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read vendor type by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_vendor_type_by_guid(vendor_type_guid: str) -> PersistenceResponse:
    """
    Retrieves a vendor type by guid from the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadVendorTypeByGUID (?)}"
                row = cursor.execute(sql, vendor_type_guid).fetchone()
                if row:
                    return PersistenceResponse(
                        data=VendorType.from_db_row(row),
                        message="Vendor type found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Vendor type not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error, pyodbc.IntegrityError) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read vendor type by guid: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_vendor_type(vendor_type: VendorType) -> PersistenceResponse:
    """
    Updates a vendor type by id in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateVendorType (?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    vendor_type.id,
                    vendor_type.name
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Vendor type updated",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Vendor type not updated",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error, pyodbc.IntegrityError) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to update vendor type by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def delete_vendor_type(vendor_type: VendorType) -> PersistenceResponse:
    """
    Deletes a vendor type by id from the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL DeleteVendorType (?)}"
                rowcount = cursor.execute(sql, vendor_type.id).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Vendor type deleted",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Vendor type not deleted",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error, pyodbc.IntegrityError) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to delete vendor type by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
