"""Persistence operations for Auth refresh tokens."""

# Python Standard Library Imports
from typing import Optional
import logging

# Third-party Imports
import pyodbc

# Local Imports
from entities.auth.business.model import AuthRefreshTokenRecord
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class AuthRefreshTokenRepository:
    """Repository responsible for refresh token persistence."""

    def _from_db(self, row: pyodbc.Row) -> Optional[AuthRefreshTokenRecord]:
        if not row:
            return None

        return AuthRefreshTokenRecord(
            id=row.Id,
            auth_id=row.AuthId,
            token_hash=row.TokenHash,
            token_jti=str(row.TokenJti) if row.TokenJti is not None else None,
            issued_datetime=row.IssuedDatetime,
            expires_datetime=row.ExpiresDatetime,
            revoked_datetime=row.RevokedDatetime,
            replaced_by_token_jti=str(row.ReplacedByTokenJti) if row.ReplacedByTokenJti is not None else None,
        )

    def create_refresh_token(
        self,
        *,
        auth_id: int,
        token_hash: str,
        token_jti: str,
        issued_datetime,
        expires_datetime,
        revoked_datetime=None,
        replaced_by_token_jti: Optional[str] = None,
    ) -> AuthRefreshTokenRecord:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateAuthRefreshToken",
                    params={
                        "AuthId": auth_id,
                        "TokenHash": token_hash,
                        "TokenJti": token_jti,
                        "IssuedDatetime": issued_datetime,
                        "ExpiresDatetime": expires_datetime,
                        "RevokedDatetime": revoked_datetime,
                        "ReplacedByTokenJti": replaced_by_token_jti,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateAuthRefreshToken failed.")
                    raise map_database_error(Exception("CreateAuthRefreshToken failed."))
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during create refresh token: %s", error)
            raise map_database_error(error)

    def read_by_hash(self, token_hash: str) -> Optional[AuthRefreshTokenRecord]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadAuthRefreshTokenByHash",
                    params={"TokenHash": token_hash},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read refresh token by hash: %s", error)
            raise map_database_error(error)

    def revoke_by_hash(
        self,
        *,
        token_hash: str,
        revoked_datetime,
        replaced_by_token_jti: Optional[str] = None,
    ) -> Optional[AuthRefreshTokenRecord]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="RevokeAuthRefreshTokenByHash",
                    params={
                        "TokenHash": token_hash,
                        "RevokedDatetime": revoked_datetime,
                        "ReplacedByTokenJti": replaced_by_token_jti,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during revoke refresh token by hash: %s", error)
            raise map_database_error(error)
