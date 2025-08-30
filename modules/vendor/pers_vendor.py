"""
Module for vendor.
"""

# python standard library imports
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# third party imports
import pyodbc

# local imports
from shared.database import get_db_connection
from shared.response import PersistenceResponse


@dataclass
class Vendor:
    """Represents a vendor in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    name: Optional[str] = None
    abbreviation: Optional[str] = None
    tax_id_number: Optional[str] = None
    is_active: Optional[bool] = None
    type: Optional[str] = None
    contact_id: Optional[int] = None
    address_id: Optional[int] = None
    payment_term_id: Optional[int] = None
    transaction_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> Optional['Vendor']:
        """Creates a Vendor object from a database row."""
        return cls(
            id=getattr(row, 'Id'),
            guid=getattr(row, 'GUID'),
            created_datetime=getattr(row, 'CreatedDatetime'),
            modified_datetime=getattr(row, 'ModifiedDatetime'),
            name=getattr(row, 'Name'),
            abbreviation=getattr(row, 'Abbreviation'),
            tax_id_number=getattr(row, 'TaxIdNumber'),
            is_active=getattr(row, 'IsActive'),
            type=getattr(row, 'Type'),
            contact_id=getattr(row, 'ContactId'),
            address_id=getattr(row, 'AddressId'),
            payment_term_id=getattr(row, 'PaymentTermId'),
            transaction_id=getattr(row, 'TransactionId')
        )


def create_vendor(vendor: Vendor) -> PersistenceResponse:
    """
    Creates a vendor in the database.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateVendor (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    vendor.created_datetime,
                    vendor.modified_datetime,
                    vendor.name,
                    vendor.abbreviation,
                    vendor.tax_id_number,
                    vendor.is_active,
                    vendor.type,
                    vendor.contact_id,
                    vendor.address_id,
                    vendor.payment_term_id
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Vendor created",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Vendor not created",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create vendor: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_vendors() -> PersistenceResponse:
    """
    Retrieves all vendors from the database.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadVendors}"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[Vendor.from_db_row(row) for row in rows],
                        message="Vendors found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No vendors found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read vendors: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_vendor_by_guid(vendor_guid: str) -> PersistenceResponse:
    """
    Retrieves a vendor by GUID from the database.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadVendorByGuid (?)}"
                row = cursor.execute(sql, vendor_guid).fetchone()
                if row:
                    return PersistenceResponse(
                        data=Vendor.from_db_row(row),
                        message="Vendor found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Vendor not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read vendor by GUID: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_vendor_by_id(vendor_id: int) -> PersistenceResponse:
    """
    Retrieves a vendor by Id from the database.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadVendorById (?)}"
                row = cursor.execute(sql, vendor_id).fetchone()
                if row:
                    return PersistenceResponse(
                        data=Vendor.from_db_row(row),
                        message="Vendor found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Vendor not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read vendor by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_vendor_by_name(vendor_name: str) -> PersistenceResponse:
    """
    Retrieves a vendor by name from the database.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadVendorByName (?)}"
                row = cursor.execute(sql, vendor_name).fetchone()
                if row:
                    return PersistenceResponse(
                        data=Vendor.from_db_row(row),
                        message="Vendor found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Vendor not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read vendor by name: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_vendor_by_id(vendor: Vendor) -> PersistenceResponse:
    """
    Updates a vendor by id in the database.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateVendorById (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    vendor.id,
                    vendor.guid,
                    vendor.created_datetime,
                    vendor.modified_datetime,
                    vendor.name,
                    vendor.abbreviation,
                    vendor.tax_id_number,
                    vendor.is_active,
                    vendor.type,
                    vendor.contact_id,
                    vendor.address_id,
                    vendor.payment_term_id,
                    vendor.transaction_id
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Vendor updated",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Vendor not updated",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to update vendor by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
