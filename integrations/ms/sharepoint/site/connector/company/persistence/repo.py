# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.ms.sharepoint.site.connector.company.business.model import SiteCompany
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class SiteCompanyRepository:
    """
    Repository for SiteCompany persistence operations.
    """

    def __init__(self):
        """Initialize the SiteCompanyRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[SiteCompany]:
        """
        Convert a database row into a SiteCompany dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return SiteCompany(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                company_id=getattr(row, "CompanyId", None),
                site_id=getattr(row, "SiteId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during site company mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during site company mapping: {error}")
            raise map_database_error(error)

    def create(self, *, company_id: int, site_id: int) -> SiteCompany:
        """
        Create a new SiteCompany mapping record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateSiteCompany",
                        params={
                            "CompanyId": company_id,
                            "SiteId": site_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateSiteCompany did not return a row.")
                        raise map_database_error(Exception("CreateSiteCompany failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create site company: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[SiteCompany]:
        """
        Read a SiteCompany mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadSiteCompanyById",
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
            logger.error(f"Error during read site company by ID: {error}")
            raise map_database_error(error)

    def read_by_company_id(self, company_id: int) -> Optional[SiteCompany]:
        """
        Read a SiteCompany mapping record by Company ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadSiteCompanyByCompanyId",
                        params={"CompanyId": company_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read site company by company ID: {error}")
            raise map_database_error(error)

    def read_by_site_id(self, site_id: int) -> Optional[SiteCompany]:
        """
        Read a SiteCompany mapping record by Site ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadSiteCompanyBySiteId",
                        params={"SiteId": site_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read site company by site ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[SiteCompany]:
        """
        Delete a SiteCompany mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteSiteCompanyById",
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
            logger.error(f"Error during delete site company by ID: {error}")
            raise map_database_error(error)

    def delete_by_company_id(self, company_id: int) -> Optional[SiteCompany]:
        """
        Delete a SiteCompany mapping record by Company ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteSiteCompanyByCompanyId",
                        params={"CompanyId": company_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during delete site company by company ID: {error}")
            raise map_database_error(error)
