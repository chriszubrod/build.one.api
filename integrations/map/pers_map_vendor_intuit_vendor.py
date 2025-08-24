"""
This module contains the persistence layer for the Map Vendor Intuit Vendor.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import pyodbc

import persistence.pers_database as pers_database
from persistence.pers_response import DatabaseError, SuccessResponse, PersistenceResponse


@dataclass
class MapVendorIntuitVendor:
    """Represents a Map Vendor Intuit Vendor in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    vendor_id: Optional[int] = None
    intuit_vendor_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> 'MapVendorIntuitVendor':
        """Creates a MapVendorIntuitVendor object from a database row."""
        if not row:
            return None

        return cls(
            id=getattr(row, 'Id'),
            guid=getattr(row, 'GUID'),
            created_datetime=getattr(row, 'CreatedDatetime'),
            modified_datetime=getattr(row, 'ModifiedDatetime'),
            vendor_id=getattr(row, 'VendorId'),
            intuit_vendor_id=getattr(row, 'IntuitVendorId'),
        )


def create_map_vendor_intuit_vendor(
        vendor_id: int,
        intuit_vendor_id: int
    ) -> PersistenceResponse:
    """
    Creates a Map Vendor Intuit Vendor in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateMapVendorIntuitVendor (?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    vendor_id,
                    intuit_vendor_id
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Map Vendor Intuit Vendor created",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Map Vendor Intuit Vendor not created",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except pyodbc.Error as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create Map Vendor Intuit Vendor: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_map_vendor_intuit_vendors() -> PersistenceResponse:
    """
    Retrieves all Map Vendor Intuit Vendors from the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMapVendorIntuitVendors}"
                rows = cursor.execute(sql).fetchall()

                if rows:
                    return PersistenceResponse(
                        data=[MapVendorIntuitVendor.from_db_row(row) for row in rows],
                        message="Map Vendor Intuit Vendors found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=[],
                    message="No Map Vendor Intuit Vendors found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Map Vendor Intuit Vendors: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_map_vendor_intuit_vendor_by_vendor_id(vendor_id: int) -> PersistenceResponse:
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMapVendorIntuitVendorByVendorId (?)}"
                row = cursor.execute(sql, int(vendor_id)).fetchone()

                if row:
                    return PersistenceResponse(
                        data=MapVendorIntuitVendor.from_db_row(row),
                        message="Map Vendor Intuit Vendor found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=[],
                    message="No Map Vendor Intuit Vendor found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Map Vendor Intuit Vendor: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_map_vendor_intuit_vendor_by_intuit_vendor_id(intuit_vendor_id: int) -> PersistenceResponse:
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMapVendorIntuitVendorByIntuitVendorId (?)}"
                rows = cursor.execute(sql, int(intuit_vendor_id)).fetchone()

                if rows:
                    return PersistenceResponse(
                        data=[MapVendorIntuitVendor.from_db_row(row) for row in rows],
                        message="Map Vendor Intuit Vendors found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )

                return PersistenceResponse(
                    data=[],
                    message="No Map Vendor Intuit Vendor found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now()
                )

        except pyodbc.Error as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Map Vendor Intuit Vendor: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_map_vendor_intuit_vendor(map_vendor_intuit_vendor):
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateMapVendorIntuitVendorById (?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    map_vendor_intuit_vendor.id,
                    map_vendor_intuit_vendor.vendor_id,
                    map_vendor_intuit_vendor.intuit_vendor_id
                ).rowcount
                cnxn.commit()

                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Map Vendor Intuit Vendor updated",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Map Vendor Intuit Vendor not updated",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except pyodbc.Error as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to update Map Vendor Intuit Vendor: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


