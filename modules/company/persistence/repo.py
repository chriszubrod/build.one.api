# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from modules.company.business.model import Company
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class CompanyRepository:
    """
    Repository for Company persistence operations.
    """

    def __init__(self):
        """Initialize the CompanyRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Company]:
        """
        Convert a database row into a Company dataclass.
        """
        if not row:
            return None

        try:
            return Company(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                name=row.Name,
                website=row.Website,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during company mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during company mapping: {error}")
            raise map_database_error(error)

    def create(self, *, name: str, website: str) -> Company:
        """
        Create a new company.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateCompany",
                    params={
                        "Name": name,
                        "Website": website,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateCompany did not return a row.")
                    raise map_database_error(Exception("CreateCompany failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create company: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[Company]:
        """
        Read all companies.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCompanies",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all companies: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: str) -> Optional[Company]:
        """
        Read a company by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCompanyById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read company by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Company]:
        """
        Read a company by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCompanyByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read company by public ID: {error}")
            raise map_database_error(error)

    def read_by_name(self, name: str) -> Optional[Company]:
        """
        Read a company by name.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadCompanyByName",
                    params={"Name": name},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read company by name: {error}")
            raise map_database_error(error)

    def update_by_id(self, company: Company) -> Optional[Company]:
        """
        Update a company by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateCompanyById",
                    params={
                        "Id": company.id,
                        "RowVersion": company.row_version_bytes,
                        "Name": company.name,
                        "Website": company.website,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update company by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: str) -> Optional[Company]:
        """
        Delete a company by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteCompanyById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete company by ID: {error}")
            raise map_database_error(error)
