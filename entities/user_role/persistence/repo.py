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
                company_id=getattr(row, "CompanyId", None),
                created_by_user_id=getattr(row, "CreatedByUserId", None),
                modified_by_user_id=getattr(row, "ModifiedByUserId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during user role mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during user role mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        user_id: int,
        role_id: int,
        company_id: Optional[int] = None,
        created_by_user_id: Optional[int] = None,
        modified_by_user_id: Optional[int] = None,
    ) -> UserRole:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateUserRole",
                    params={
                        "UserId": user_id,
                        "RoleId": role_id,
                        "CompanyId": company_id,
                        "CreatedByUserId": created_by_user_id,
                        "ModifiedByUserId": modified_by_user_id,
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

    def read_by_id(self, id: int) -> Optional[UserRole]:
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

    def read_by_user_id(self, user_id: int) -> Optional[UserRole]:
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

    def read_all_by_user_id(self, user_id: int) -> list[UserRole]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserRolesByUserId",
                    params={"UserId": user_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all user roles by user ID: {error}")
            raise map_database_error(error)

    def read_all_by_user_id_and_company_id(
        self, *, user_id: int, company_id: int
    ) -> list[UserRole]:
        """
        Phase 2 permission resolver: returns ALL UserRole rows for the
        (user, company) pair so the resolver can OR their RoleModule
        grants together.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserRolesByUserIdAndCompanyId",
                    params={"UserId": user_id, "CompanyId": company_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(
                f"Error during read user roles by user+company: {error}"
            )
            raise map_database_error(error)

    def read_by_role_id(self, role_id: int) -> Optional[UserRole]:
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
                        "CompanyId": user_role.company_id,
                        "ModifiedByUserId": user_role.modified_by_user_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update user role by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[UserRole]:
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
