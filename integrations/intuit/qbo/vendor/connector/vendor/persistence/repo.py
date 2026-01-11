# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.vendor.connector.vendor.business.model import VendorVendor
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class VendorVendorRepository:
    """
    Repository for VendorVendor persistence operations.
    """

    def __init__(self):
        """Initialize the VendorVendorRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[VendorVendor]:
        """
        Convert a database row into a VendorVendor dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return VendorVendor(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                vendor_id=getattr(row, "VendorId", None),
                qbo_vendor_id=getattr(row, "QboVendorId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during vendor vendor mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during vendor vendor mapping: {error}")
            raise map_database_error(error)

    def create(self, *, vendor_id: int, qbo_vendor_id: int) -> VendorVendor:
        """
        Create a new VendorVendor mapping record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateVendorVendor",
                        params={
                            "VendorId": vendor_id,
                            "QboVendorId": qbo_vendor_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateVendorVendor did not return a row.")
                        raise map_database_error(Exception("CreateVendorVendor failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create vendor vendor: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[VendorVendor]:
        """
        Read a VendorVendor mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadVendorVendorById",
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
            logger.error(f"Error during read vendor vendor by ID: {error}")
            raise map_database_error(error)

    def read_by_vendor_id(self, vendor_id: int) -> Optional[VendorVendor]:
        """
        Read a VendorVendor mapping record by Vendor ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadVendorVendorByVendorId",
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
            logger.error(f"Error during read vendor vendor by vendor ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_vendor_id(self, qbo_vendor_id: int) -> Optional[VendorVendor]:
        """
        Read a VendorVendor mapping record by QboVendor ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadVendorVendorByQboVendorId",
                        params={"QboVendorId": qbo_vendor_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read vendor vendor by QBO vendor ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, vendor_vendor: VendorVendor) -> Optional[VendorVendor]:
        """
        Update a VendorVendor mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="UpdateVendorVendorById",
                        params={
                            "Id": vendor_vendor.id,
                            "RowVersion": vendor_vendor.row_version_bytes,
                            "VendorId": vendor_vendor.vendor_id,
                            "QboVendorId": vendor_vendor.qbo_vendor_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("UpdateVendorVendorById did not return a row.")
                        raise map_database_error(Exception("UpdateVendorVendorById failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update vendor vendor by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[VendorVendor]:
        """
        Delete a VendorVendor mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteVendorVendorById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during delete vendor vendor by ID: {error}")
            raise map_database_error(error)
