# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.company.business.model import Company
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
                organization_id=getattr(row, "OrganizationId", None),
                created_by_user_id=getattr(row, "CreatedByUserId", None),
                modified_by_user_id=getattr(row, "ModifiedByUserId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during company mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during company mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        name: str,
        website: str,
        organization_id: Optional[int] = None,
        created_by_user_id: Optional[int] = None,
        modified_by_user_id: Optional[int] = None,
    ) -> Company:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateCompany",
                    params={
                        "Name": name,
                        "Website": website,
                        "OrganizationId": organization_id,
                        "CreatedByUserId": created_by_user_id,
                        "ModifiedByUserId": modified_by_user_id,
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

    def read_by_id(self, id: int) -> Optional[Company]:
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
                        "OrganizationId": company.organization_id,
                        "ModifiedByUserId": company.modified_by_user_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update company by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[Company]:
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
