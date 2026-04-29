# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.organization_company.business.model import OrganizationCompany
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class OrganizationCompanyRepository:
    """Repository for OrganizationCompany persistence operations."""

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[OrganizationCompany]:
        if not row:
            return None
        try:
            return OrganizationCompany(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                organization_id=row.OrganizationId,
                company_id=row.CompanyId,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during organization company mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during organization company mapping: {error}")
            raise map_database_error(error)

    def create(self, *, organization_id: int, company_id: int) -> OrganizationCompany:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateOrganizationCompany",
                    params={
                        "OrganizationId": organization_id,
                        "CompanyId": company_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateOrganizationCompany did not return a row.")
                    raise map_database_error(Exception("CreateOrganizationCompany failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create organization company: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[OrganizationCompany]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadOrganizationCompanies", params={})
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all organization companies: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[OrganizationCompany]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadOrganizationCompanyById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read organization company by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[OrganizationCompany]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadOrganizationCompanyByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read organization company by public ID: {error}")
            raise map_database_error(error)

    def read_all_by_organization_id(self, organization_id: int) -> list[OrganizationCompany]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadOrganizationCompaniesByOrganizationId",
                    params={"OrganizationId": organization_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all organization companies by organization ID: {error}")
            raise map_database_error(error)

    def read_all_by_company_id(self, company_id: int) -> list[OrganizationCompany]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadOrganizationCompaniesByCompanyId",
                    params={"CompanyId": company_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all organization companies by company ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, organization_company: OrganizationCompany) -> Optional[OrganizationCompany]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateOrganizationCompanyById",
                    params={
                        "Id": organization_company.id,
                        "RowVersion": organization_company.row_version_bytes,
                        "OrganizationId": organization_company.organization_id,
                        "CompanyId": organization_company.company_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update organization company by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[OrganizationCompany]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteOrganizationCompanyById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete organization company by ID: {error}")
            raise map_database_error(error)
