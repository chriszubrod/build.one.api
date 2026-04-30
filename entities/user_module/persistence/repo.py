# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.user_module.business.model import UserModule
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class UserModuleRepository:
    """
    Repository for UserModule persistence operations.
    """

    def __init__(self):
        """Initialize the UserModuleRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[UserModule]:
        if not row:
            return None

        try:
            return UserModule(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                user_id=row.UserId,
                module_id=row.ModuleId,
                company_id=getattr(row, "CompanyId", None),
                created_by_user_id=getattr(row, "CreatedByUserId", None),
                modified_by_user_id=getattr(row, "ModifiedByUserId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during user module mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during user module mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        user_id: int,
        module_id: int,
        company_id: Optional[int] = None,
        created_by_user_id: Optional[int] = None,
        modified_by_user_id: Optional[int] = None,
    ) -> UserModule:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateUserModule",
                    params={
                        "UserId": user_id,
                        "ModuleId": module_id,
                        "CompanyId": company_id,
                        "CreatedByUserId": created_by_user_id,
                        "ModifiedByUserId": modified_by_user_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateUserModule did not return a row.")
                    raise map_database_error(Exception("CreateUserModule failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create user module: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[UserModule]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadUserModules", params={})
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all user modules: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[UserModule]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserModuleById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user module by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[UserModule]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserModuleByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user module by public ID: {error}")
            raise map_database_error(error)

    def read_by_user_id(self, user_id: int) -> Optional[UserModule]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserModuleByUserId",
                    params={"UserId": user_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user module by user ID: {error}")
            raise map_database_error(error)

    def read_all_by_user_id(self, user_id: int) -> list[UserModule]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserModulesByUserId",
                    params={"UserId": user_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all user modules by user ID: {error}")
            raise map_database_error(error)

    def read_all_by_user_id_and_company_id(
        self, *, user_id: int, company_id: int
    ) -> list[UserModule]:
        """
        Phase 2 permission resolver: returns the user's additive
        UserModule grants scoped to the active Company.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserModulesByUserIdAndCompanyId",
                    params={"UserId": user_id, "CompanyId": company_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(
                f"Error during read user modules by user+company: {error}"
            )
            raise map_database_error(error)

    def read_by_module_id(self, module_id: int) -> Optional[UserModule]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserModuleByModuleId",
                    params={"ModuleId": module_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user module by module ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, user_module: UserModule) -> Optional[UserModule]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateUserModuleById",
                    params={
                        "Id": user_module.id,
                        "RowVersion": user_module.row_version_bytes,
                        "UserId": user_module.user_id,
                        "ModuleId": user_module.module_id,
                        "CompanyId": user_module.company_id,
                        "ModifiedByUserId": user_module.modified_by_user_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update user module by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[UserModule]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteUserModuleById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete user module by ID: {error}")
            raise map_database_error(error)
