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
            return QboAuth(
                code=getattr(row, "Code", None),
                realm_id=getattr(row, "RealmId", None),
                state=getattr(row, "State", None),
                token_type=getattr(row, "TokenType", None),
                id_token=getattr(row, "IdToken", None),
                access_token=getattr(row, "AccessToken", None),
                expires_in=getattr(row, "ExpiresIn", None),
                refresh_token=getattr(row, "RefreshToken", None),
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
        Create a new QboAuth.
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
                        "IdToken": id_token,
                        "AccessToken": access_token,
                        "ExpiresIn": expires_in,
                        "RefreshToken": refresh_token,
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
        Update a QboAuth by realm ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateQboAuthByRealmId",
                    params={
                        "Code": code,
                        "RealmId": realm_id,
                        "State": state,
                        "TokenType": token_type,
                        "IdToken": id_token,
                        "AccessToken": access_token,
                        "ExpiresIn": expires_in,
                        "RefreshToken": refresh_token,
                        "XRefreshTokenExpiresIn": x_refresh_token_expires_in,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Update qbo auth did not return a row.")
                    raise map_database_error(Exception("update qbo auth by realm ID failed"))
                return self._from_db(row)
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
