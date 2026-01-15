# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.ms.auth.business.model import MsAuth
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)

class MsAuthRepository:
    """
    Repository for MsAuth persistence operations.
    """

    def __init__(self):
        """Initialize the MsAuthRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[MsAuth]:
        """
        Convert a database row into a MsAuth dataclass.
        """
        if not row:
            return None

        try:
            return MsAuth(
                id=getattr(row, "Id", None),
                public_id=getattr(row, "PublicId", None),
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                code=getattr(row, "Code", None),
                state=getattr(row, "State", None),
                token_type=getattr(row, "TokenType", None),
                access_token=getattr(row, "AccessToken", None),
                expires_in=getattr(row, "ExpiresIn", None),
                refresh_token=getattr(row, "RefreshToken", None),
                scope=getattr(row, "Scope", None),
                tenant_id=getattr(row, "TenantId", None),
                user_id=getattr(row, "UserId", None)
            )
        except AttributeError as error:
            logger.error("Attribute error during ms auth mapping: %s", error)
            raise map_database_error(error)
        except Exception as error:
            logger.error("Unexpected error during ms auth mapping: %s", error)
            raise map_database_error(error)

    def create(self, *, code: str, state: str, token_type: str, access_token: str, expires_in: int, refresh_token: str, scope: str, tenant_id: str, user_id: Optional[str] = None) -> MsAuth:
        """
        Create a new MsAuth.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateMsAuth",
                    params={
                        "Code": code,
                        "State": state,
                        "TokenType": token_type,
                        "AccessToken": access_token,
                        "ExpiresIn": expires_in,
                        "RefreshToken": refresh_token,
                        "Scope": scope,
                        "TenantId": tenant_id,
                        "UserId": user_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Create ms auth did not return a row.")
                    raise map_database_error(Exception("create ms auth failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during create ms auth: %s", error)
            raise map_database_error(error)

    def read_all(self) -> list[MsAuth]:
        """
        Read all MsAuths.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsAuths",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read all ms auths: %s", error)
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[MsAuth]:
            """
            Read a MsAuth by ID.
            """
            try:
                with get_connection() as conn:
                    cursor = conn.cursor()
                    call_procedure(
                        cursor=cursor,
                        name="ReadMsAuthById",
                        params={
                            "Id": id,
                        },
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
            except Exception as error:
                logger.error("Error during read ms auth by ID: %s", error)
                raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[MsAuth]:
            """
            Read a MsAuth by public ID.
            """
            try:
                with get_connection() as conn:
                    cursor = conn.cursor()
                    call_procedure(
                        cursor=cursor,
                        name="ReadMsAuthByPublicId",
                        params={
                            "PublicId": public_id,
                        },
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
            except Exception as error:
                logger.error("Error during read ms auth by public ID: %s", error)
                raise map_database_error(error)

    def read_by_tenant_id(self, tenant_id: str) -> Optional[MsAuth]:
        """
        Read a MsAuth by tenant ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsAuthByTenantId",
                    params={
                        "TenantId": tenant_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read ms auth by tenant ID: %s", error)
            raise map_database_error(error)

    def update_by_tenant_id(self, code: str, state: str, token_type: str, access_token: str, expires_in: int, refresh_token: str, scope: str, tenant_id: str, user_id: Optional[str] = None) -> Optional[MsAuth]:
        """
        Update a MsAuth by tenant ID.
        """
        cursor = None
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="UpdateMsAuthByTenantId",
                        params={
                            "Code": code,
                            "State": state,
                            "TokenType": token_type,
                            "AccessToken": access_token,
                            "ExpiresIn": expires_in,
                            "RefreshToken": refresh_token,
                            "Scope": scope,
                            "TenantId": tenant_id,
                            "UserId": user_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Update ms auth did not return a row.")
                        error_msg = "update ms auth by tenant ID failed - no matching record found"
                        raise Exception(error_msg)
                    result = self._from_db(row)
                    return result
                finally:
                    if cursor:
                        try:
                            cursor.close()
                        except:
                            pass
        except Exception as error:
            logger.error("Error during update ms auth by tenant ID: %s", error)
            raise map_database_error(error)

    def delete_by_tenant_id(self, tenant_id: str) -> Optional[MsAuth]:
        """
        Delete a MsAuth by tenant ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteMsAuthByTenantId",
                    params={
                        "TenantId": tenant_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Delete ms auth did not return a row.")
                    raise map_database_error(Exception("delete ms auth by tenant ID failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during delete ms auth by tenant ID: %s", error)
            raise map_database_error(error)
