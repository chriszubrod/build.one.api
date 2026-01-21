# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.ms.sharepoint.driveitem.connector.project_excel.business.model import DriveItemProjectExcel
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class DriveItemProjectExcelRepository:
    """
    Repository for DriveItemProjectExcel persistence operations.
    """

    def __init__(self):
        """Initialize the DriveItemProjectExcelRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[DriveItemProjectExcel]:
        """
        Convert a database row into a DriveItemProjectExcel dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return DriveItemProjectExcel(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                project_id=getattr(row, "ProjectId", None),
                ms_driveitem_id=getattr(row, "MsDriveItemId", None),
                worksheet_name=getattr(row, "WorksheetName", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during driveitem project excel mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during driveitem project excel mapping: {error}")
            raise map_database_error(error)

    def create(self, *, project_id: int, ms_driveitem_id: int, worksheet_name: str) -> DriveItemProjectExcel:
        """
        Create a new DriveItemProjectExcel mapping record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateDriveItemProjectExcel",
                        params={
                            "ProjectId": project_id,
                            "MsDriveItemId": ms_driveitem_id,
                            "WorksheetName": worksheet_name,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateDriveItemProjectExcel did not return a row.")
                        raise map_database_error(Exception("CreateDriveItemProjectExcel failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create driveitem project excel: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[DriveItemProjectExcel]:
        """
        Read a DriveItemProjectExcel mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadDriveItemProjectExcelById",
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
            logger.error(f"Error during read driveitem project excel by ID: {error}")
            raise map_database_error(error)

    def read_by_project_id(self, project_id: int) -> Optional[DriveItemProjectExcel]:
        """
        Read a DriveItemProjectExcel mapping record by Project ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadDriveItemProjectExcelByProjectId",
                        params={"ProjectId": project_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read driveitem project excel by project ID: {error}")
            raise map_database_error(error)

    def read_by_ms_driveitem_id(self, ms_driveitem_id: int) -> Optional[DriveItemProjectExcel]:
        """
        Read a DriveItemProjectExcel mapping record by MS DriveItem ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadDriveItemProjectExcelByMsDriveItemId",
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
            logger.error(f"Error during read driveitem project excel by ms driveitem ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[DriveItemProjectExcel]:
        """
        Delete a DriveItemProjectExcel mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteDriveItemProjectExcelById",
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
            logger.error(f"Error during delete driveitem project excel by ID: {error}")
            raise map_database_error(error)

    def delete_by_project_id(self, project_id: int) -> Optional[DriveItemProjectExcel]:
        """
        Delete a DriveItemProjectExcel mapping record by Project ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteDriveItemProjectExcelByProjectId",
                        params={"ProjectId": project_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during delete driveitem project excel by project ID: {error}")
            raise map_database_error(error)
