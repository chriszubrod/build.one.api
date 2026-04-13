# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.vendor_type.business.model import VendorType
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class VendorTypeRepository:
    """
    Repository for VendorType persistence operations.
    """

    def __init__(self):
        """Initialize the VendorTypeRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[VendorType]:
        """
        Convert a database row into a VendorType dataclass.
        """
        if not row:
            return None

        try:
            return VendorType(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                name=row.Name,
                description=row.Description,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during vendor type mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during vendor type mapping: {error}")
            raise map_database_error(error)

    def create(self, *, name: Optional[str], description: Optional[str]) -> VendorType:
        """
        Create a new vendor type.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateVendorType",
                    params={
                        "Name": name,
                        "Description": description,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateVendorType did not return a row.")
                    raise map_database_error(Exception("CreateVendorType failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create vendor type: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[VendorType]:
        """
        Read all vendor types.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorTypes",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all vendor types: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[VendorType]:
        """
        Read a vendor type by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorTypeById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read vendor type by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[VendorType]:
        """
        Read a vendor type by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorTypeByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read vendor type by public ID: {error}")
            raise map_database_error(error)

    def read_by_name(self, name: str) -> Optional[VendorType]:
        """
        Read a vendor type by name.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorTypeByName",
                    params={"Name": name},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read vendor type by name: {error}")
            raise map_database_error(error)

    def update_by_id(self, vendor_type: VendorType) -> Optional[VendorType]:
        """
        Update a vendor type by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateVendorTypeById",
                    params={
                        "Id": vendor_type.id,
                        "RowVersion": vendor_type.row_version_bytes,
                        "Name": vendor_type.name,
                        "Description": vendor_type.description,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update vendor type by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[VendorType]:
        """
        Delete a vendor type by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteVendorTypeById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete vendor type by ID: {error}")
            raise map_database_error(error)
