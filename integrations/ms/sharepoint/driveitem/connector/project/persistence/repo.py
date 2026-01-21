# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.ms.sharepoint.driveitem.connector.project.business.model import DriveItemProject
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class DriveItemProjectRepository:
    """
    Repository for DriveItemProject persistence operations.
    """

    def __init__(self):
        """Initialize the DriveItemProjectRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[DriveItemProject]:
        """
        Convert a database row into a DriveItemProject dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return DriveItemProject(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                project_id=getattr(row, "ProjectId", None),
                ms_driveitem_id=getattr(row, "MsDriveItemId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during driveitem project mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during driveitem project mapping: {error}")
            raise map_database_error(error)

    def create(self, *, project_id: int, ms_driveitem_id: int) -> DriveItemProject:
        """
        Create a new DriveItemProject mapping record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateDriveItemProject",
                        params={
                            "ProjectId": project_id,
                            "MsDriveItemId": ms_driveitem_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateDriveItemProject did not return a row.")
                        raise map_database_error(Exception("CreateDriveItemProject failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create driveitem project: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[DriveItemProject]:
        """
        Read a DriveItemProject mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadDriveItemProjectById",
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
            logger.error(f"Error during read driveitem project by ID: {error}")
            raise map_database_error(error)

    def read_by_project_id(self, project_id: int) -> Optional[DriveItemProject]:
        """
        Read a DriveItemProject mapping record by Project ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadDriveItemProjectByProjectId",
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
            logger.error(f"Error during read driveitem project by project ID: {error}")
            raise map_database_error(error)

    def read_by_ms_driveitem_id(self, ms_driveitem_id: int) -> Optional[DriveItemProject]:
        """
        Read a DriveItemProject mapping record by MS DriveItem ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadDriveItemProjectByMsDriveItemId",
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
            logger.error(f"Error during read driveitem project by ms driveitem ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[DriveItemProject]:
        """
        Delete a DriveItemProject mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteDriveItemProjectById",
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
            logger.error(f"Error during delete driveitem project by ID: {error}")
            raise map_database_error(error)

    def delete_by_project_id(self, project_id: int) -> Optional[DriveItemProject]:
        """
        Delete a DriveItemProject mapping record by Project ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteDriveItemProjectByProjectId",
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
            logger.error(f"Error during delete driveitem project by project ID: {error}")
            raise map_database_error(error)
