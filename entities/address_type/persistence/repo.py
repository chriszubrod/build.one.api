# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.address_type.business.model import AddressType
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class AddressTypeRepository:
    """
    Repository for AddressType persistence operations.
    """

    def __init__(self):
        """Initialize the AddressTypeRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[AddressType]:
        """
        Convert a database row into a AddressType dataclass.
        """
        if not row:
            return None

        try:
            return AddressType(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                name=row.Name,
                description=row.Description,
                display_order=row.DisplayOrder,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during address type mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during address type mapping: {error}")
            raise map_database_error(error)

    def create(self, *, name: str, description: str, display_order: int) -> AddressType:
        """
        Create a new address type.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateAddressType",
                    params={
                        "Name": name,
                        "Description": description,
                        "DisplayOrder": display_order,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateAddressType did not return a row.")
                    raise map_database_error(Exception("CreateAddressType failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create address type: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[AddressType]:
        """
        Read all address types.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadAddressTypes",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all address types: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[AddressType]:
        """
        Read an address type by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadAddressTypeById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read address type by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[AddressType]:
        """
        Read an address type by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadAddressTypeByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read address type by public ID: {error}")
            raise map_database_error(error)

    def read_by_name(self, name: str) -> Optional[AddressType]:
        """
        Read an address type by name.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadAddressTypeByName",
                    params={"Name": name},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read address type by name: {error}")
            raise map_database_error(error)

    def update_by_id(self, address_type: AddressType) -> Optional[AddressType]:
        """
        Update an address type by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateAddressTypeById",
                    params={
                        "Id": address_type.id,
                        "RowVersion": address_type.row_version_bytes,
                        "Name": address_type.name,
                        "Description": address_type.description,
                        "DisplayOrder": address_type.display_order,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update address type by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[AddressType]:
        """
        Delete an address type by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteAddressTypeById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete address type by ID: {error}")
            raise map_database_error(error)
