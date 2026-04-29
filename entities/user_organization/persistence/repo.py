# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.user_organization.business.model import UserOrganization
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class UserOrganizationRepository:
    """Repository for UserOrganization persistence operations."""

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[UserOrganization]:
        if not row:
            return None
        try:
            return UserOrganization(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                user_id=row.UserId,
                organization_id=row.OrganizationId,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during user organization mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during user organization mapping: {error}")
            raise map_database_error(error)

    def create(self, *, user_id: int, organization_id: int) -> UserOrganization:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateUserOrganization",
                    params={
                        "UserId": user_id,
                        "OrganizationId": organization_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateUserOrganization did not return a row.")
                    raise map_database_error(Exception("CreateUserOrganization failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create user organization: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[UserOrganization]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadUserOrganizations", params={})
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all user organizations: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[UserOrganization]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserOrganizationById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user organization by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[UserOrganization]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserOrganizationByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user organization by public ID: {error}")
            raise map_database_error(error)

    def read_by_user_id(self, user_id: int) -> Optional[UserOrganization]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserOrganizationByUserId",
                    params={"UserId": user_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user organization by user ID: {error}")
            raise map_database_error(error)

    def read_all_by_user_id(self, user_id: int) -> list[UserOrganization]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserOrganizationsByUserId",
                    params={"UserId": user_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all user organizations by user ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, user_organization: UserOrganization) -> Optional[UserOrganization]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateUserOrganizationById",
                    params={
                        "Id": user_organization.id,
                        "RowVersion": user_organization.row_version_bytes,
                        "UserId": user_organization.user_id,
                        "OrganizationId": user_organization.organization_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update user organization by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[UserOrganization]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteUserOrganizationById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete user organization by ID: {error}")
            raise map_database_error(error)
