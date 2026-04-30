# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.user_company.business.model import UserCompany
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class UserCompanyRepository:
    """Repository for UserCompany persistence operations."""

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[UserCompany]:
        if not row:
            return None
        try:
            return UserCompany(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                user_id=row.UserId,
                company_id=row.CompanyId,
                created_by_user_id=getattr(row, "CreatedByUserId", None),
                modified_by_user_id=getattr(row, "ModifiedByUserId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during user company mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during user company mapping: {error}")
            raise map_database_error(error)

    def create(
        self,
        *,
        user_id: int,
        company_id: int,
        created_by_user_id: Optional[int] = None,
        modified_by_user_id: Optional[int] = None,
    ) -> UserCompany:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateUserCompany",
                    params={
                        "UserId": user_id,
                        "CompanyId": company_id,
                        "CreatedByUserId": created_by_user_id,
                        "ModifiedByUserId": modified_by_user_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateUserCompany did not return a row.")
                    raise map_database_error(Exception("CreateUserCompany failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create user company: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[UserCompany]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadUserCompanies", params={})
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all user companies: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[UserCompany]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserCompanyById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user company by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[UserCompany]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserCompanyByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user company by public ID: {error}")
            raise map_database_error(error)

    def read_by_user_id(self, user_id: int) -> Optional[UserCompany]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserCompanyByUserId",
                    params={"UserId": user_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user company by user ID: {error}")
            raise map_database_error(error)

    def read_all_by_user_id(self, user_id: int) -> list[UserCompany]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserCompaniesByUserId",
                    params={"UserId": user_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all user companies by user ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, user_company: UserCompany) -> Optional[UserCompany]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateUserCompanyById",
                    params={
                        "Id": user_company.id,
                        "RowVersion": user_company.row_version_bytes,
                        "UserId": user_company.user_id,
                        "CompanyId": user_company.company_id,
                        "ModifiedByUserId": user_company.modified_by_user_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update user company by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[UserCompany]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteUserCompanyById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete user company by ID: {error}")
            raise map_database_error(error)
