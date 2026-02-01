# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from services.address.business.model import Address, Country
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class AddressRepository:
    """
    Repository for Address persistence operations.
    """

    def __init__(self):
        """Initialize the AddressRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Address]:
        """
        Convert a database row into a Address dataclass.
        """
        if not row:
            return None

        try:
            # Country is always United States
            country = Country.UNITED_STATES
            
            return Address(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                street_one=row.StreetOne,
                street_two=row.StreetTwo,
                city=row.City,
                state=row.State,
                zip=row.Zip,
                country=country,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during address mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during address mapping: {error}")
            raise map_database_error(error)

    def create(self, *, street_one: str, street_two: Optional[str] = None, city: str, state: str, zip: str, country: Country) -> Address:
        """
        Create a new address.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                # For now, we'll pass the country abbreviation to the stored procedure
                # The stored procedure should handle finding/creating the country record
                call_procedure(
                    cursor=cursor,
                    name="CreateAddress",
                    params={
                        "StreetOne": street_one,
                        "StreetTwo": street_two,
                        "City": city,
                        "State": state,
                        "Zip": zip,
                        "Country": country.country_name if hasattr(country, 'country_name') else (country.value.get("name") if isinstance(country.value, dict) else str(country)),
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateAddress did not return a row.")
                    raise map_database_error(Exception("CreateAddress failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create address: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[Address]:
        """
        Read all addresses.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadAddresses",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all addresses: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: str) -> Optional[Address]:
        """
        Read an address by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadAddressById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read address by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Address]:
        """
        Read an address by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadAddressByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read address by public ID: {error}")
            raise map_database_error(error)

    def read_by_street_one_and_city(self, street_one: str, city: str) -> Optional[Address]:
        """
        Read an address by street one and city.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadAddressByStreetOneAndCity",
                    params={"StreetOne": street_one, "City": city},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read address by street one and city: {error}")
            raise map_database_error(error)

    def update_by_id(self, address: Address) -> Optional[Address]:
        """
        Update an address by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                # For now, we'll pass the country abbreviation to the stored procedure
                # The stored procedure should handle finding/updating the country record
                call_procedure(
                    cursor=cursor,
                    name="UpdateAddressById",
                    params={
                        "Id": address.id,
                        "RowVersion": address.row_version_bytes,
                        "StreetOne": address.street_one,
                        "StreetTwo": address.street_two,
                        "City": address.city,
                        "State": address.state,
                        "Zip": address.zip,
                        "Country": address.country.country_name if address.country and hasattr(address.country, 'country_name') else (address.country.value.get("name") if address.country and isinstance(address.country.value, dict) else (Country.UNITED_STATES.country_name if not address.country else str(address.country))),
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update address by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: str) -> Optional[Address]:
        """
        Delete an address by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteAddressById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete address by ID: {error}")
            raise map_database_error(error)
