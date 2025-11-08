# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from modules.role_module.business.model import RoleModule
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class RoleModuleRepository:
    """
    Repository for RoleModule persistence operations.
    """

    def __init__(self):
        """Initialize the RoleModuleRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[RoleModule]:
        """
        Convert a database row into a RoleModule dataclass.
        """
        if not row:
            return None

        try:
            return RoleModule(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                role_id=row.RoleId,
                module_id=row.ModuleId,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during role module mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during role module mapping: {error}")
            raise map_database_error(error)

    def create(self, *, role_id: str, module_id: str) -> RoleModule:
        """
        Create a new role module.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateRoleModule",
                    params={
                        "RoleId": role_id,
                        "ModuleId": module_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateRoleModule did not return a row.")
                    raise map_database_error(Exception("CreateRoleModule failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create role module: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[RoleModule]:
        """
        Read all role modules.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadRoleModules",
                    params={}
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all role modules: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: str) -> Optional[RoleModule]:
        """
        Read a role module by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadRoleModuleById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read role module by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[RoleModule]:
        """
        Read a role module by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadRoleModuleByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read role module by public ID: {error}")
            raise map_database_error(error)

    def read_by_role_id(self, role_id: str) -> Optional[RoleModule]:
        """
        Read a role module by role ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadRoleModuleByRoleId",
                    params={"RoleId": role_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read role module by role ID: {error}")
            raise map_database_error(error)

    def read_by_module_id(self, module_id: str) -> Optional[RoleModule]:
        """
        Read a role module by module ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadRoleModuleByModuleId",
                    params={"ModuleId": module_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read role module by module ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, role_module: RoleModule) -> Optional[RoleModule]:
        """
        Update a role module by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateRoleModuleById",
                    params={
                        "Id": role_module.id,
                        "RowVersion": role_module.row_version_bytes,
                        "RoleId": role_module.role_id,
                        "ModuleId": role_module.module_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update role module by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: str) -> Optional[RoleModule]:
        """
        Delete a role module by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteRoleModuleById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete role module by ID: {error}")
            raise map_database_error(error)
