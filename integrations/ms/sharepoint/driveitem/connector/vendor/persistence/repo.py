# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.ms.sharepoint.driveitem.connector.vendor.business.model import DriveItemVendor
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class DriveItemVendorRepository:
    """
    Repository for DriveItemVendor persistence operations.
    """

    def __init__(self):
        """Initialize the DriveItemVendorRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[DriveItemVendor]:
        """
        Convert a database row into a DriveItemVendor dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return DriveItemVendor(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                vendor_id=getattr(row, "VendorId", None),
                ms_driveitem_id=getattr(row, "MsDriveItemId", None),
            )
        except AttributeError as error:
            logger.error("Attribute error during driveitem vendor mapping: %s", error)
            raise map_database_error(error)
        except Exception as error:
            logger.error("Unexpected error during driveitem vendor mapping: %s", error)
            raise map_database_error(error)

    def create(self, *, vendor_id: int, ms_driveitem_id: int) -> DriveItemVendor:
        """
        Create a new DriveItemVendor mapping record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateDriveItemVendor",
                        params={
                            "VendorId": vendor_id,
                            "MsDriveItemId": ms_driveitem_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateDriveItemVendor did not return a row.")
                        raise map_database_error(Exception("CreateDriveItemVendor failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error("Error during create driveitem vendor: %s", error)
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[DriveItemVendor]:
        """Read by ID."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadDriveItemVendorById",
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
            logger.error("Error during read driveitem vendor by ID: %s", error)
            raise map_database_error(error)

    def read_by_vendor_id(self, vendor_id: int) -> Optional[DriveItemVendor]:
        """Read by Vendor ID."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadDriveItemVendorByVendorId",
                        params={"VendorId": vendor_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error("Error during read driveitem vendor by vendor ID: %s", error)
            raise map_database_error(error)

    def read_by_ms_driveitem_id(self, ms_driveitem_id: int) -> Optional[DriveItemVendor]:
        """Read by MS DriveItem ID."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadDriveItemVendorByMsDriveItemId",
                        params={"MsDriveItemId": ms_driveitem_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error("Error during read driveitem vendor by ms driveitem ID: %s", error)
            raise map_database_error(error)

    def delete_by_vendor_id(self, vendor_id: int) -> Optional[DriveItemVendor]:
        """Delete by Vendor ID. Returns deleted row if any."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteDriveItemVendorByVendorId",
                        params={"VendorId": vendor_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error("Error during delete driveitem vendor by vendor ID: %s", error)
            raise map_database_error(error)

