# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.ms.sharepoint.drive.connector.company.business.model import DriveCompany
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class DriveCompanyRepository:
    """
    Repository for DriveCompany persistence operations.
    """

    def __init__(self):
        """Initialize the DriveCompanyRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[DriveCompany]:
        """
        Convert a database row into a DriveCompany dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return DriveCompany(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                company_id=getattr(row, "CompanyId", None),
                ms_drive_id=getattr(row, "MsDriveId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during drive company mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during drive company mapping: {error}")
            raise map_database_error(error)

    def create(self, *, company_id: int, ms_drive_id: int) -> DriveCompany:
        """
        Create a new DriveCompany mapping record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateDriveCompany",
                        params={
                            "CompanyId": company_id,
                            "MsDriveId": ms_drive_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateDriveCompany did not return a row.")
                        raise map_database_error(Exception("CreateDriveCompany failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create drive company: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[DriveCompany]:
        """
        Read a DriveCompany mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadDriveCompanyById",
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
            logger.error(f"Error during read drive company by ID: {error}")
            raise map_database_error(error)

    def read_by_company_id(self, company_id: int) -> Optional[DriveCompany]:
        """
        Read a DriveCompany mapping record by Company ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadDriveCompanyByCompanyId",
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
            logger.error(f"Error during read drive company by company ID: {error}")
            raise map_database_error(error)

    def read_by_ms_drive_id(self, ms_drive_id: int) -> Optional[DriveCompany]:
        """
        Read a DriveCompany mapping record by MS Drive ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadDriveCompanyByMsDriveId",
                        params={"MsDriveId": ms_drive_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read drive company by ms drive ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[DriveCompany]:
        """
        Delete a DriveCompany mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteDriveCompanyById",
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
            logger.error(f"Error during delete drive company by ID: {error}")
            raise map_database_error(error)

    def delete_by_company_id(self, company_id: int) -> Optional[DriveCompany]:
        """
        Delete a DriveCompany mapping record by Company ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteDriveCompanyByCompanyId",
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
            logger.error(f"Error during delete drive company by company ID: {error}")
            raise map_database_error(error)
