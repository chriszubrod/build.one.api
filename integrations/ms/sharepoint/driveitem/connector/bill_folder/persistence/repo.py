# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.ms.sharepoint.driveitem.connector.bill_folder.business.model import DriveItemBillFolder
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class DriveItemBillFolderRepository:
    """
    Repository for DriveItemBillFolder persistence operations.
    """

    def __init__(self):
        """Initialize the DriveItemBillFolderRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[DriveItemBillFolder]:
        """
        Convert a database row into a DriveItemBillFolder dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return DriveItemBillFolder(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                company_id=getattr(row, "CompanyId", None),
                ms_driveitem_id=getattr(row, "MsDriveItemId", None),
                folder_type=getattr(row, "FolderType", None),
            )
        except AttributeError as error:
            logger.error("Attribute error during driveitem bill folder mapping: %s", error)
            raise map_database_error(error)
        except Exception as error:
            logger.error("Unexpected error during driveitem bill folder mapping: %s", error)
            raise map_database_error(error)

    def create(self, *, company_id: int, ms_driveitem_id: int, folder_type: str) -> DriveItemBillFolder:
        """
        Create a new DriveItemBillFolder mapping record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateDriveItemBillFolder",
                        params={
                            "CompanyId": company_id,
                            "MsDriveItemId": ms_driveitem_id,
                            "FolderType": folder_type,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateDriveItemBillFolder did not return a row.")
                        raise map_database_error(Exception("CreateDriveItemBillFolder failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error("Error during create driveitem bill folder: %s", error)
            raise map_database_error(error)

    def read_by_company_id_and_folder_type(self, company_id: int, folder_type: str) -> Optional[DriveItemBillFolder]:
        """Read by Company ID and Folder Type."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadDriveItemBillFolderByCompanyIdAndFolderType",
                        params={
                            "CompanyId": company_id,
                            "FolderType": folder_type,
                        },
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error("Error during read driveitem bill folder by company and type: %s", error)
            raise map_database_error(error)

    def read_by_ms_driveitem_id(self, ms_driveitem_id: int) -> Optional[DriveItemBillFolder]:
        """Read by MS DriveItem ID."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadDriveItemBillFolderByMsDriveItemId",
                        params={"MsDriveItemId": ms_driveitem_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error("Error during read driveitem bill folder by ms driveitem ID: %s", error)
            raise map_database_error(error)

    def delete_by_company_id_and_folder_type(self, company_id: int, folder_type: str) -> Optional[DriveItemBillFolder]:
        """Delete by Company ID and Folder Type. Returns deleted row if any."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteDriveItemBillFolderByCompanyIdAndFolderType",
                        params={
                            "CompanyId": company_id,
                            "FolderType": folder_type,
                        },
                    )
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error("Error during delete driveitem bill folder by company and type: %s", error)
            raise map_database_error(error)
