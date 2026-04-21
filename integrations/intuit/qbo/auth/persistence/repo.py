# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.auth.business.model import QboAuth
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)
from shared.encryption import decrypt_if_encrypted, encrypt_sensitive_data

logger = logging.getLogger(__name__)

class QboAuthRepository:
    """
    Repository for QboAuth persistence operations.
    """

    def __init__(self):
        """Initialize the QboAuthRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[QboAuth]:
        """
        Convert a database row into a QboAuth dataclass.
        """
        if not row:
            return None

        try:
            # Tokens are encrypted at rest via Fernet. Legacy plaintext rows
            # pass through decrypt_if_encrypted unchanged until their next write.
            return QboAuth(
                id=getattr(row, "Id", None),
                public_id=getattr(row, "PublicId", None),
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                code=getattr(row, "Code", None),
                realm_id=getattr(row, "RealmId", None),
                state=getattr(row, "State", None),
                token_type=getattr(row, "TokenType", None),
                id_token=decrypt_if_encrypted(getattr(row, "IdToken", None)),
                access_token=decrypt_if_encrypted(getattr(row, "AccessToken", None)),
                expires_in=getattr(row, "ExpiresIn", None),
                refresh_token=decrypt_if_encrypted(getattr(row, "RefreshToken", None)),
                x_refresh_token_expires_in=getattr(row, "XRefreshTokenExpiresIn", None)
            )
        except AttributeError as error:
            logger.error("Attribute error during qbo auth mapping: %s", error)
            raise map_database_error(error)
        except Exception as error:
            logger.error("Unexpected error during qbo auth mapping: %s", error)
            raise map_database_error(error)

    def create(self, *, code: str, realm_id: str, state: str, token_type: str, id_token: str, access_token: str, expires_in: int, refresh_token: str, x_refresh_token_expires_in: int) -> QboAuth:
        """
        Create a new QboAuth. Token fields are encrypted at rest via Fernet.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateQboAuth",
                    params={
                        "Code": code,
                        "RealmId": realm_id,
                        "State": state,
                        "TokenType": token_type,
                        "IdToken": encrypt_sensitive_data(id_token),
                        "AccessToken": encrypt_sensitive_data(access_token),
                        "ExpiresIn": expires_in,
                        "RefreshToken": encrypt_sensitive_data(refresh_token),
                        "XRefreshTokenExpiresIn": x_refresh_token_expires_in,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Create qbo auth did not return a row.")
                    raise map_database_error(Exception("create qbo auth failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during create qbo auth: %s", error)
            raise map_database_error(error)

    def read_all(self) -> list[QboAuth]:
        """
        Read all QboAuths.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadQboAuths",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read all qbo auths: %s", error)
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[QboAuth]:
            """
            Read a QboAuth by ID.
            """
            try:
                with get_connection() as conn:
                    cursor = conn.cursor()
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboAuthById",
                        params={
                            "Id": id,
                        },
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
            except Exception as error:
                logger.error("Error during read qbo auth by ID: %s", error)
                raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[QboAuth]:
            """
            Read a QboAuth by public ID.
            """
            try:
                with get_connection() as conn:
                    cursor = conn.cursor()
                    call_procedure(
                        cursor=cursor,
                        name="ReadQboAuthByPublicId",
                        params={
                            "PublicId": public_id,
                        },
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
            except Exception as error:
                logger.error("Error during read qbo auth by public ID: %s", error)
                raise map_database_error(error)

    def read_by_realm_id(self, realm_id: str) -> Optional[QboAuth]:
        """
        Read a QboAuth by realm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadQboAuthByRealmId",
                    params={
                        "RealmId": realm_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read qbo auth by realm ID: %s", error)
            raise map_database_error(error)

    def update_by_realm_id(self, code: str, realm_id: str, state: str, token_type: str, id_token: str, access_token: str, expires_in: int, refresh_token: str, x_refresh_token_expires_in: int) -> Optional[QboAuth]:
        """
        Update a QboAuth by realm ID. Token fields are encrypted at rest via Fernet.
        """
        cursor = None
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="UpdateQboAuthByRealmId",
                        params={
                            "Code": code,
                            "RealmId": realm_id,
                            "State": state,
                            "TokenType": token_type,
                            "IdToken": encrypt_sensitive_data(id_token),
                            "AccessToken": encrypt_sensitive_data(access_token),
                            "ExpiresIn": expires_in,
                            "RefreshToken": encrypt_sensitive_data(refresh_token),
                            "XRefreshTokenExpiresIn": x_refresh_token_expires_in,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("Update qbo auth did not return a row.")
                        error_msg = "update qbo auth by realm ID failed - no matching record found"
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
            logger.error("Error during update qbo auth by realm ID: %s", error)
            raise map_database_error(error)

    def delete_by_realm_id(self, realm_id: str) -> Optional[QboAuth]:
        """
        Delete a QboAuth by realm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteQboAuthByRealmId",
                    params={
                        "RealmId": realm_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Delete qbo auth did not return a row.")
                    raise map_database_error(Exception("delete qbo auth by realm ID failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during delete qbo auth by realm ID: %s", error)
            raise map_database_error(error)
