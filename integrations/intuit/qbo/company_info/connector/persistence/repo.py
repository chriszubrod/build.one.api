# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.company_info.connector.business.model import CompanyInfoCompany
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class CompanyInfoCompanyRepository:
    """
    Repository for CompanyInfoCompany persistence operations.
    """

    def __init__(self):
        """Initialize the CompanyInfoCompanyRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[CompanyInfoCompany]:
        """
        Convert a database row into a CompanyInfoCompany dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return CompanyInfoCompany(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                company_id=getattr(row, "CompanyId", None),
                qbo_company_info_id=getattr(row, "QboCompanyInfoId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during company info company mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during company info company mapping: {error}")
            raise map_database_error(error)

    def create(self, *, company_id: int, qbo_company_info_id: int) -> CompanyInfoCompany:
        """
        Create a new CompanyInfoCompany mapping record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateCompanyInfoCompany",
                        params={
                            "CompanyId": company_id,
                            "QboCompanyInfoId": qbo_company_info_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateCompanyInfoCompany did not return a row.")
                        raise map_database_error(Exception("CreateCompanyInfoCompany failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create company info company: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[CompanyInfoCompany]:
        """
        Read a CompanyInfoCompany mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadCompanyInfoCompanyById",
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
            logger.error(f"Error during read company info company by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[CompanyInfoCompany]:
        """
        Read a CompanyInfoCompany mapping record by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadCompanyInfoCompanyByPublicId",
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
            logger.error(f"Error during read company info company by public ID: {error}")
            raise map_database_error(error)

    def read_by_company_id(self, company_id: int) -> Optional[CompanyInfoCompany]:
        """
        Read a CompanyInfoCompany mapping record by Company ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadCompanyInfoCompanyByCompanyId",
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
            logger.error(f"Error during read company info company by company ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_company_info_id(self, qbo_company_info_id: int) -> Optional[CompanyInfoCompany]:
        """
        Read a CompanyInfoCompany mapping record by QboCompanyInfo ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadCompanyInfoCompanyByQboCompanyInfoId",
                        params={"QboCompanyInfoId": qbo_company_info_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read company info company by QBO company info ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, company_info_company: CompanyInfoCompany) -> Optional[CompanyInfoCompany]:
        """
        Update a CompanyInfoCompany mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="UpdateCompanyInfoCompanyById",
                        params={
                            "Id": company_info_company.id,
                            "RowVersion": company_info_company.row_version_bytes,
                            "CompanyId": company_info_company.company_id,
                            "QboCompanyInfoId": company_info_company.qbo_company_info_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("UpdateCompanyInfoCompanyById did not return a row.")
                        raise map_database_error(Exception("UpdateCompanyInfoCompanyById failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during update company info company by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[CompanyInfoCompany]:
        """
        Delete a CompanyInfoCompany mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteCompanyInfoCompanyById",
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
            logger.error(f"Error during delete company info company by ID: {error}")
            raise map_database_error(error)

