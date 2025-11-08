# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from mappings.vendor_qbo_vendor.business.model import MapVendorQboVendor
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class MapVendorQboVendorRepository:
    """
    Repository for MapVendorQboVendor persistence operations.
    """

    def __init__(self):
        """Initialize the MapVendorQboVendorRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[MapVendorQboVendor]:
        """
        Convert a database row into a MapVendorQboVendor dataclass.
        """
        if not row:
            return None

        try:
            return MapVendorQboVendor(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                vendor_id=row.VendorId,
                qbo_vendor_id=row.QboVendorId
            )
        except AttributeError as error:
            logger.error(f"Attribute error during map vendor qbo vendor mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during map vendor qbo vendor mapping: {error}")
            raise map_database_error(error)

    def create(self, *, vendor_id: Optional[str], qbo_vendor_id: Optional[str]) -> MapVendorQboVendor:
        """
        Create a new map vendor qbo vendor record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateVendorQboVendor",
                    params={
                        "VendorId": vendor_id,
                        "QboVendorId": qbo_vendor_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateVendorQboVendor did not return a row.")
                    raise map_database_error(Exception("CreateVendorQboVendor failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create map vendor qbo vendor: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[MapVendorQboVendor]:
        """
        Read all map vendor qbo vendor records.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorQboVendors",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all map vendor qbo vendors: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: str) -> Optional[MapVendorQboVendor]:
        """
        Read a map vendor qbo vendor record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorQboVendorById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read map vendor qbo vendor by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[MapVendorQboVendor]:
        """
        Read a map vendor qbo vendor record by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorQboVendorByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read map vendor qbo vendor by public ID: {error}")
            raise map_database_error(error)

    def read_by_vendor_id(self, vendor_id: str) -> Optional[MapVendorQboVendor]:
        """
        Read a map vendor qbo vendor record by vendor ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorQboVendorByVendorId",
                    params={"VendorId": vendor_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read map vendor qbo vendor by vendor ID: {error}")
            raise map_database_error(error)
    
    def read_by_qbo_vendor_id(self, qbo_vendor_id: str) -> Optional[MapVendorQboVendor]:
        """
        Read a map vendor qbo vendor record by vendor ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorQboVendorByQboVendorId",
                    params={"QboVendorId": qbo_vendor_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read map vendor qbo vendor by qbo vendor ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, map_vendor_qbo_vendor: MapVendorQboVendor) -> Optional[MapVendorQboVendor]:
        """
        Update a map vendor qbo vendor record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateVendorQboVendorById",
                    params={
                        "Id": map_vendor_qbo_vendor.id,
                        "RowVersion": map_vendor_qbo_vendor.row_version_bytes,
                        "VendorId": map_vendor_qbo_vendor.vendor_id,
                        "QboVendorId": map_vendor_qbo_vendor.qbo_vendor_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update map vendor qbo vendor by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: str) -> Optional[MapVendorQboVendor]:
        """
        Delete a map vendor qbo vendor record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteVendorQboVendorById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete map vendor qbo vendor by ID: {error}")
            raise map_database_error(error)
