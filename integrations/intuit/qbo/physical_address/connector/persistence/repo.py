# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.physical_address.connector.business.model import PhysicalAddressAddress
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class PhysicalAddressAddressRepository:
    """
    Repository for PhysicalAddressAddress persistence operations.
    """

    def __init__(self):
        """Initialize the PhysicalAddressAddressRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[PhysicalAddressAddress]:
        """
        Convert a database row into a PhysicalAddressAddress dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return PhysicalAddressAddress(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                address_id=getattr(row, "AddressId", None),
                qbo_physical_address_id=getattr(row, "QboPhysicalAddressId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during physical address address mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during physical address address mapping: {error}")
            raise map_database_error(error)

    def create(self, *, address_id: int, qbo_physical_address_id: int) -> PhysicalAddressAddress:
        """
        Create a new PhysicalAddressAddress mapping record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreatePhysicalAddressAddress",
                        params={
                            "AddressId": address_id,
                            "QboPhysicalAddressId": qbo_physical_address_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreatePhysicalAddressAddress did not return a row.")
                        raise map_database_error(Exception("CreatePhysicalAddressAddress failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create physical address address: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[PhysicalAddressAddress]:
        """
        Read a PhysicalAddressAddress mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadPhysicalAddressAddressById",
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
            logger.error(f"Error during read physical address address by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[PhysicalAddressAddress]:
        """
        Read a PhysicalAddressAddress mapping record by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadPhysicalAddressAddressByPublicId",
                        params={"PublicId": public_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read physical address address by public ID: {error}")
            raise map_database_error(error)

    def read_by_address_id(self, address_id: int) -> Optional[PhysicalAddressAddress]:
        """
        Read a PhysicalAddressAddress mapping record by Address ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadPhysicalAddressAddressByAddressId",
                        params={"AddressId": address_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read physical address address by address ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_physical_address_id(self, qbo_physical_address_id: int) -> Optional[PhysicalAddressAddress]:
        """
        Read a PhysicalAddressAddress mapping record by QboPhysicalAddress ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadPhysicalAddressAddressByQboPhysicalAddressId",
                        params={"QboPhysicalAddressId": qbo_physical_address_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read physical address address by QBO physical address ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, mapping: PhysicalAddressAddress) -> Optional[PhysicalAddressAddress]:
        """
        Update a PhysicalAddressAddress mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="UpdatePhysicalAddressAddressById",
                        params={
                            "Id": mapping.id,
                            "RowVersion": mapping.row_version_bytes,
                            "AddressId": mapping.address_id,
                            "QboPhysicalAddressId": mapping.qbo_physical_address_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("UpdatePhysicalAddressAddressById did not return a row.")
                        raise map_database_error(Exception("UpdatePhysicalAddressAddressById failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update physical address address by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[PhysicalAddressAddress]:
        """
        Delete a PhysicalAddressAddress mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeletePhysicalAddressAddressById",
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
            logger.error(f"Error during delete physical address address by ID: {error}")
            raise map_database_error(error)

