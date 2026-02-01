# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from services.vendor_address.business.model import VendorAddress
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class VendorAddressRepository:
    """
    Repository for VendorAddress persistence operations.
    """

    def __init__(self):
        """Initialize the VendorAddressRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[VendorAddress]:
        """
        Convert a database row into a VendorAddress dataclass.
        """
        if not row:
            return None

        try:
            return VendorAddress(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                vendor_id=row.VendorId,
                address_id=row.AddressId,
                address_type_id=row.AddressTypeId,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during vendor address mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during vendor address mapping: {error}")
            raise map_database_error(error)

    def create(self, *, vendor_id: str, address_id: str, address_type_id: str) -> VendorAddress:
        """
        Create a new vendor address.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateVendorAddress",
                    params={
                        "VendorId": vendor_id,
                        "AddressId": address_id,
                        "AddressTypeId": address_type_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateVendorAddress did not return a row.")
                    raise map_database_error(Exception("CreateVendorAddress failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create vendor address: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[VendorAddress]:
        """
        Read all vendor addresses.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorAddresses",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all vendor addresses: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: str) -> Optional[VendorAddress]:
        """
        Read a vendor address by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorAddressById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read vendor address by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[VendorAddress]:
        """
        Read a vendor address by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorAddressByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read vendor address by public ID: {error}")
            raise map_database_error(error)

    def read_by_vendor_id(self, vendor_id: str) -> Optional[VendorAddress]:
        """
        Read a vendor address by vendor ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorAddressByVendorId",
                    params={"VendorId": vendor_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read vendor address by vendor ID: {error}")
            raise map_database_error(error)
    
    def read_by_address_id(self, address_id: str) -> Optional[VendorAddress]:
        """
        Read a vendor address by address ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorAddressByAddressId",
                    params={"AddressId": address_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read vendor address by address ID: {error}")
            raise map_database_error(error)
    
    def read_by_address_type_id(self, address_type_id: str) -> Optional[VendorAddress]:
        """
        Read a vendor address by address type ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorAddressByAddressTypeId",
                    params={"AddressTypeId": address_type_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read vendor address by address type ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, vendor_address: VendorAddress) -> Optional[VendorAddress]:
        """
        Update a vendor address by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateVendorAddressById",
                    params={
                        "Id": vendor_address.id,
                        "RowVersion": vendor_address.row_version_bytes,
                        "VendorId": vendor_address.vendor_id,
                        "AddressId": vendor_address.address_id,
                        "AddressTypeId": vendor_address.address_type_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update vendor address by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: str) -> Optional[VendorAddress]:
        """
        Delete a vendor address by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteVendorAddressById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during delete vendor address by ID: {error}")
            raise map_database_error(error)
    
    def delete_by_vendor_id(self, vendor_id: int) -> None:
        """
        Delete all vendor addresses for a vendor by vendor ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteVendorAddressByVendorId",
                    params={"VendorId": vendor_id},
                )
        except Exception as error:
            logger.error(f"Error during delete vendor addresses by vendor ID: {error}")
            raise map_database_error(error)