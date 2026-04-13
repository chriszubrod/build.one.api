"""Persistence operations for the Auth module."""

# Python Standard Library Imports
from typing import List, Optional
import base64
import logging

# Third-party Imports
import pyodbc

# Local Imports
from entities.auth.business.model import Auth

from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class AuthRepository:
    """Repository responsible for all persistence access to the Auth data model."""

    def __init__(self):
        """Initialize the AuthRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Auth]:
        """
        Convert database row to Auth model.
        """
        if not row:
            return None

        return Auth(
            id=row.Id,
            public_id=row.PublicId,
            row_version=base64.b64encode(row.RowVersion).decode("ascii"),
            created_datetime=row.CreatedDatetime,
            modified_datetime=row.ModifiedDatetime,
            username=row.Username,
            password_hash=row.PasswordHash,
            user_id=row.UserId
        )

    def create(self, *, username: str, password_hash: str) -> Auth:
        """
        Create a new auth record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateAuth",
                    params={
                        "Username": username,
                        "PasswordHash": password_hash
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Create Auth failed.")
                    raise map_database_error(Exception("Create Auth failed."))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create auth: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Auth]:
        """
        Read auth by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadAuthByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read auth by public ID: {error}")
            raise map_database_error(error)

    def read_by_username(self, username: str) -> Optional[Auth]:
        """
        Read auth by username.

        Retrieves a specific auth by its username value using the
        ReadAuthByUsername stored procedure.

        Args:
            username: Auth to search for

        Returns:
            Auth object if found, None otherwise

        Raises:
            DatabaseError: If database operation fails
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadAuthByUsername",
                    params={"Username": username},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read auth by username: {error}")
            raise map_database_error(error)

    def update_by_id(self, auth: Auth) -> Optional[Auth]:
        """
        Update auth by ID with optimistic concurrency control.

        Updates an existing auth using the UpdateAuthById stored procedure.
        Uses row version for optimistic concurrency control to prevent conflicts.

        Args:
            auth: Auth object with updated data and current row version

        Returns:
            Updated Auth object if successful, None if not found or version mismatch

        Raises:
            DatabaseError: If database operation fails
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateAuthById",
                    params={
                        "Id": auth.id,
                        "RowVersion": auth.row_version_bytes,
                        "Username": auth.username,
                        "PasswordHash": auth.password_hash,
                        "UserId": auth.user_id
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update auth by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[Auth]:
        """
        Hard delete auth by ID.

        Permanently removes an auth record using the DeleteAuthById stored procedure.

        Args:
            id: Auth internal identifier

        Returns:
            Deleted Auth object if found, None otherwise

        Raises:
            DatabaseError: If database operation fails
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteAuthById",
                    params={
                        "Id": id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete auth by ID: {error}")
            raise map_database_error(error)


