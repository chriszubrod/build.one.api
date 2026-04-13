# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.role.business.model import Role
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class RoleRepository:
    """
    Repository for Role persistence operations.
    """

    def __init__(self):
        """Initialize the RoleRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Role]:
        """
        Convert a database row into a Role dataclass.
        """
        if not row:
            return None

        try:
            return Role(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                name=row.Name,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during role mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during role mapping: {error}")
            raise map_database_error(error)

    def create(self, *, name: str) -> Role:
        """
        Create a new role.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateRole",
                    params={
                        "Name": name,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateRole did not return a row.")
                    raise map_database_error(Exception("CreateRole failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create role: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[Role]:
        """
        Read all roles.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadRoles",
                    params={}
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all roles: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[Role]:
        """
        Read a role by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadRoleById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read role by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Role]:
        """
        Read a role by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadRoleByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read role by public ID: {error}")
            raise map_database_error(error)

    def read_by_name(self, name: str) -> Optional[Role]:
        """
        Read a role by name.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadRoleByName",
                    params={"Name": name},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read role by name: {error}")
            raise map_database_error(error)

    def update_by_id(self, role: Role) -> Optional[Role]:
        """
        Update a role by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateRoleById",
                    params={
                        "Id": role.id,
                        "RowVersion": role.row_version_bytes,
                        "Name": role.name,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update role by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[Role]:
        """
        Delete a role by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteRoleById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete role by ID: {error}")
            raise map_database_error(error)
