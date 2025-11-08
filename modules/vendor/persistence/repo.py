# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from modules.vendor.business.model import Vendor
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class VendorRepository:
    """
    Repository for Vendor persistence operations.
    """

    def __init__(self):
        """Initialize the VendorRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Vendor]:
        """
        Convert a database row into a Vendor dataclass.
        """
        if not row:
            return None

        try:
            return Vendor(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                name=row.Name,
                abbreviation=row.Abbreviation,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during vendor mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during vendor mapping: {error}")
            raise map_database_error(error)

    def create(self, *, name: Optional[str], abbreviation: Optional[str]) -> Vendor:
        """
        Create a new vendor.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateVendor",
                    params={
                        "Name": name,
                        "Abbreviation": abbreviation,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateVendor did not return a row.")
                    raise map_database_error(Exception("CreateVendor failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create vendor: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[Vendor]:
        """
        Read all vendors.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendors",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all vendors: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: str) -> Optional[Vendor]:
        """
        Read a vendor by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read vendor by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Vendor]:
        """
        Read a vendor by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read vendor by public ID: {error}")
            raise map_database_error(error)

    def read_by_name(self, name: str) -> Optional[Vendor]:
        """
        Read a vendor by name.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorByName",
                    params={"Name": name},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read vendor by name: {error}")
            raise map_database_error(error)

    def update_by_id(self, vendor: Vendor) -> Optional[Vendor]:
        """
        Update a vendor by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateVendorById",
                    params={
                        "Id": vendor.id,
                        "RowVersion": vendor.row_version_bytes,
                        "Name": vendor.name,
                        "Abbreviation": vendor.abbreviation,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update vendor by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: str) -> Optional[Vendor]:
        """
        Delete a vendor by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteVendorById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete vendor by ID: {error}")
            raise map_database_error(error)
