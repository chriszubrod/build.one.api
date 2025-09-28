"""Persistence layer for mapping vendors to Intuit vendors."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import pyodbc

from integrations.adapters import register_adapter
from shared.database import get_db_connection
from shared.response import PersistenceResponse


@register_adapter
@dataclass
class MapVendorToIntuitVendor:
    """Represents a vendor to Intuit vendor mapping in the system."""

    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    vendor_id: Optional[int] = None
    intuit_vendor_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> 'MapVendorToIntuitVendor':
        """Create an instance from a database row."""

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


def create_map_vendor_to_intuit_vendor(
    vendor_id: int,
    intuit_vendor_id: int,
) -> PersistenceResponse:
    """Create a vendor to Intuit vendor mapping in the database."""

    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateMapVendorIntuitVendor (?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    vendor_id,
                    intuit_vendor_id,
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Vendor to Intuit vendor mapping created",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now(),
                    )
                cnxn.rollback()
                return PersistenceResponse(
                    data=None,
                    message="Vendor to Intuit vendor mapping not created",
                    status_code=400,
                    success=False,
                    timestamp=datetime.now(),
                )

        except pyodbc.Error as exc:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=(
                    "Failed to create vendor to Intuit vendor mapping: "
                    f"{exc}"
                ),
                status_code=500,
                success=False,
                timestamp=datetime.now(),
            )


def read_map_vendor_to_intuit_vendors() -> PersistenceResponse:
    """Retrieve all vendor to Intuit vendor mappings from the database."""

    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMapVendorIntuitVendors}"
                rows = cursor.execute(sql).fetchall()

                if rows:
                    return PersistenceResponse(
                        data=[MapVendorToIntuitVendor.from_db_row(row) for row in rows],
                        message="Vendor to Intuit vendor mappings found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now(),
                    )

                return PersistenceResponse(
                    data=[],
                    message="No vendor to Intuit vendor mappings found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now(),
                )

        except pyodbc.Error as exc:
            return PersistenceResponse(
                data=None,
                message=(
                    "Failed to read vendor to Intuit vendor mappings: "
                    f"{exc}"
                ),
                status_code=500,
                success=False,
                timestamp=datetime.now(),
            )


def read_map_vendor_to_intuit_vendor_by_vendor_id(
    vendor_id: int,
) -> PersistenceResponse:
    """Retrieve vendor mapping by vendor identifier."""

    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMapVendorIntuitVendorByVendorId (?)}"
                row = cursor.execute(sql, int(vendor_id)).fetchone()

                if row:
                    return PersistenceResponse(
                        data=MapVendorToIntuitVendor.from_db_row(row),
                        message="Vendor to Intuit vendor mapping found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now(),
                    )

                return PersistenceResponse(
                    data=[],
                    message="No vendor to Intuit vendor mapping found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now(),
                )

        except pyodbc.Error as exc:
            return PersistenceResponse(
                data=None,
                message=(
                    "Failed to read vendor to Intuit vendor mapping: "
                    f"{exc}"
                ),
                status_code=500,
                success=False,
                timestamp=datetime.now(),
            )


def read_map_vendor_to_intuit_vendor_by_intuit_vendor_id(
    intuit_vendor_id: int,
) -> PersistenceResponse:
    """Retrieve vendor mapping by Intuit vendor identifier."""

    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMapVendorIntuitVendorByIntuitVendorId (?)}"
                rows = cursor.execute(sql, int(intuit_vendor_id)).fetchall()

                if rows:
                    return PersistenceResponse(
                        data=[MapVendorToIntuitVendor.from_db_row(row) for row in rows],
                        message="Vendor to Intuit vendor mappings found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now(),
                    )

                return PersistenceResponse(
                    data=[],
                    message="No vendor to Intuit vendor mapping found",
                    status_code=404,
                    success=False,
                    timestamp=datetime.now(),
                )

        except pyodbc.Error as exc:
            return PersistenceResponse(
                data=None,
                message=(
                    "Failed to read vendor to Intuit vendor mapping: "
                    f"{exc}"
                ),
                status_code=500,
                success=False,
                timestamp=datetime.now(),
            )


def update_map_vendor_to_intuit_vendor(
    map_vendor_to_intuit_vendor: MapVendorToIntuitVendor,
) -> PersistenceResponse:
    """Update an existing vendor to Intuit vendor mapping."""

    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateMapVendorIntuitVendorById (?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    map_vendor_to_intuit_vendor.id,
                    map_vendor_to_intuit_vendor.vendor_id,
                    map_vendor_to_intuit_vendor.intuit_vendor_id,
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=None,
                        message="Vendor to Intuit vendor mapping updated",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now(),
                    )
                cnxn.rollback()
                return PersistenceResponse(
                    data=None,
                    message="Vendor to Intuit vendor mapping not updated",
                    status_code=400,
                    success=False,
                    timestamp=datetime.now(),
                )

        except pyodbc.Error as exc:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=(
                    "Failed to update vendor to Intuit vendor mapping: "
                    f"{exc}"
                ),
                status_code=500,
                success=False,
                timestamp=datetime.now(),
            )
