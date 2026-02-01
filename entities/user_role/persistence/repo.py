# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.user_role.business.model import UserRole
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class UserRoleRepository:
    """
    Repository for UserRole persistence operations.
    """

    def __init__(self):
        """Initialize the UserRoleRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[UserRole]:
        """
        Convert a database row into a UserRole dataclass.
        """
        if not row:
            return None

        try:
            return UserRole(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                user_id=row.UserId,
                role_id=row.RoleId,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during user role mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during user role mapping: {error}")
            raise map_database_error(error)

    def create(self, *, user_id: str, role_id: str) -> UserRole:
        """
        Create a new user role.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateUserRole",
                    params={
                        "UserId": user_id,
                        "RoleId": role_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateUserRole did not return a row.")
                    raise map_database_error(Exception("CreateUserRole failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create user role: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[UserRole]:
        """
        Read all user roles.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserRoles",
                    params={}
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all user roles: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: str) -> Optional[UserRole]:
        """
        Read a user role by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserRoleById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user role by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[UserRole]:
        """
        Read a user role by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserRoleByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user role by public ID: {error}")
            raise map_database_error(error)

    def read_by_user_id(self, user_id: str) -> Optional[UserRole]:
        """
        Read a user role by user ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserRoleByUserId",
                    params={"UserId": user_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user role by user ID: {error}")
            raise map_database_error(error)

    def read_by_role_id(self, role_id: str) -> Optional[UserRole]:
        """
        Read a user role by role ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserRoleByRoleId",
                    params={"RoleId": role_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user role by role ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, user_role: UserRole) -> Optional[UserRole]:
        """
        Update a user role by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateUserRoleById",
                    params={
                        "Id": user_role.id,
                        "RowVersion": user_role.row_version_bytes,
                        "UserId": user_role.user_id,
                        "RoleId": user_role.role_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update user role by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: str) -> Optional[UserRole]:
        """
        Delete a user role by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteUserRoleById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete user role by ID: {error}")
            raise map_database_error(error)
