# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.physical_address.business.model import QboPhysicalAddress
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class QboPhysicalAddressRepository:
    """
    Repository for QboPhysicalAddress persistence operations.
    """

    def __init__(self):
        """Initialize the QboPhysicalAddressRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[QboPhysicalAddress]:
        """
        Convert a database row into a QboPhysicalAddress dataclass.
        """
        if not row:
            return None

        try:
            return QboPhysicalAddress(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                qbo_id=row.QboId,
                line1=row.Line1,
                line2=row.Line2,
                city=row.City,
                country=row.Country,
                country_sub_division_code=row.CountrySubDivisionCode,
                postal_code=row.PostalCode,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during qbo physical address mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during qbo physical address mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        qbo_id: Optional[str],
        line1: Optional[str],
        line2: Optional[str],
        city: Optional[str],
        country: Optional[str],
        country_sub_division_code: Optional[str],
        postal_code: Optional[str],
    ) -> QboPhysicalAddress:
        """
        Create a new QboPhysicalAddress.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateQboPhysicalAddress",
                    params={
                        "QboId": qbo_id,
                        "Line1": line1,
                        "Line2": line2,
                        "City": city,    
                        "Country": country,
                        "CountrySubDivisionCode": country_sub_division_code,
                        "PostalCode": postal_code
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Create qbo physical address did not return a row.")
                    raise map_database_error(Exception("create qbo physical address failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create qbo physical address: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[QboPhysicalAddress]:
        """
        Read all QboPhysicalAddresses.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadQboPhysicalAddresses",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all qbo physical addresses: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[QboPhysicalAddress]:
        """
        Read a QboPhysicalAddress by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadQboPhysicalAddressById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read qbo physical address by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[QboPhysicalAddress]:
        """
        Read a QboPhysicalAddress by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadQboPhysicalAddressByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read qbo physical address by public ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_id(self, qbo_id: str) -> Optional[QboPhysicalAddress]:
        """
        Read a QboPhysicalAddress by QBO ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadQboPhysicalAddressByQboId",
                    params={"QboId": qbo_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read qbo physical address by QBO ID: {error}")
            raise map_database_error(error)

    def update_by_id(
        self,
        id: int,
        row_version: bytes,
        qbo_id: Optional[str],
        line1: Optional[str],
        line2: Optional[str],
        city: Optional[str],
        country: Optional[str],
        country_sub_division_code: Optional[str],
        postal_code: Optional[str],
    ) -> Optional[QboPhysicalAddress]:
        """
        Update a QboPhysicalAddress by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateQboPhysicalAddressById",
                    params={
                        "Id": id,
                        "RowVersion": row_version,
                        "QboId": qbo_id,
                        "Line1": line1,
                        "Line2": line2,
                        "City": city,
                        "Country": country,
                        "CountrySubDivisionCode": country_sub_division_code,
                        "PostalCode": postal_code,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Update qbo physical address did not return a row.")
                    raise map_database_error(Exception("update qbo physical address by ID failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update qbo physical address by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[QboPhysicalAddress]:
        """
        Delete a QboPhysicalAddress by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteQboPhysicalAddressById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Delete qbo physical address did not return a row.")
                    raise map_database_error(Exception("delete qbo physical address by ID failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during delete qbo physical address by ID: {error}")
            raise map_database_error(error)
