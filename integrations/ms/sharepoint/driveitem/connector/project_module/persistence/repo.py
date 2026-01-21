# Python Standard Library Imports
import base64
import logging
from typing import Optional, List

# Third-party Imports
import pyodbc

# Local Imports
from integrations.ms.sharepoint.driveitem.connector.project_module.business.model import DriveItemProjectModule
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class DriveItemProjectModuleRepository:
    """
    Repository for DriveItemProjectModule persistence operations.
    """

    def __init__(self):
        """Initialize the DriveItemProjectModuleRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[DriveItemProjectModule]:
        """
        Convert a database row into a DriveItemProjectModule dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return DriveItemProjectModule(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                project_id=getattr(row, "ProjectId", None),
                module_id=getattr(row, "ModuleId", None),
                ms_driveitem_id=getattr(row, "MsDriveItemId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during driveitem project module mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during driveitem project module mapping: {error}")
            raise map_database_error(error)

    def create(self, *, project_id: int, module_id: int, ms_driveitem_id: int) -> DriveItemProjectModule:
        """
        Create a new DriveItemProjectModule mapping record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateDriveItemProjectModule",
                        params={
                            "ProjectId": project_id,
                            "ModuleId": module_id,
                            "MsDriveItemId": ms_driveitem_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateDriveItemProjectModule did not return a row.")
                        raise map_database_error(Exception("CreateDriveItemProjectModule failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create driveitem project module: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[DriveItemProjectModule]:
        """
        Read a DriveItemProjectModule mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadDriveItemProjectModuleById",
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
            logger.error(f"Error during read driveitem project module by ID: {error}")
            raise map_database_error(error)

    def read_by_project_id_and_module_id(self, project_id: int, module_id: int) -> Optional[DriveItemProjectModule]:
        """
        Read a DriveItemProjectModule mapping record by Project ID and Module ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadDriveItemProjectModuleByProjectIdAndModuleId",
                        params={
                            "ProjectId": project_id,
                            "ModuleId": module_id,
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
            logger.error(f"Error during read driveitem project module by project ID and module ID: {error}")
            raise map_database_error(error)

    def read_by_project_id(self, project_id: int) -> List[DriveItemProjectModule]:
        """
        Read all DriveItemProjectModule mapping records for a Project.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadDriveItemProjectModulesByProjectId",
                        params={"ProjectId": project_id},
                    )
                    rows = cursor.fetchall()
                    return [self._from_db(row) for row in rows if row]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read driveitem project modules by project ID: {error}")
            raise map_database_error(error)

    def read_by_ms_driveitem_id(self, ms_driveitem_id: int) -> Optional[DriveItemProjectModule]:
        """
        Read a DriveItemProjectModule mapping record by MS DriveItem ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadDriveItemProjectModuleByMsDriveItemId",
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
            logger.error(f"Error during read driveitem project module by ms driveitem ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[DriveItemProjectModule]:
        """
        Delete a DriveItemProjectModule mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteDriveItemProjectModuleById",
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
            logger.error(f"Error during delete driveitem project module by ID: {error}")
            raise map_database_error(error)

    def delete_by_project_id_and_module_id(self, project_id: int, module_id: int) -> Optional[DriveItemProjectModule]:
        """
        Delete a DriveItemProjectModule mapping record by Project ID and Module ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteDriveItemProjectModuleByProjectIdAndModuleId",
                        params={
                            "ProjectId": project_id,
                            "ModuleId": module_id,
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
            logger.error(f"Error during delete driveitem project module by project ID and module ID: {error}")
            raise map_database_error(error)
