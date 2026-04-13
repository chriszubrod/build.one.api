# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.user.business.model import User
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class UserRepository:
    """
    Repository for User persistence operations.
    """

    def __init__(self):
        """Initialize the UserRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[User]:
        """
        Convert a database row into a User dataclass.
        """
        if not row:
            return None

        try:
            return User(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                firstname=row.Firstname,
                lastname=row.Lastname,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during user mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during user mapping: {error}")
            raise map_database_error(error)

    def create(self, *, firstname: str, lastname: str) -> User:
        """
        Create a new user.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateUser",
                    params={
                        "Firstname": firstname,
                        "Lastname": lastname,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateUser did not return a row.")
                    raise map_database_error(Exception("CreateUser failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create user: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[User]:
        """
        Read all users.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUsers",
                    params={}
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all users: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[User]:
        """
        Read a user by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[User]:
        """
        Read a user by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user by public ID: {error}")
            raise map_database_error(error)

    def read_by_firstname(self, firstname: str) -> Optional[User]:
        """
        Read a user by firstname.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserByFirstname",
                    params={"Firstname": firstname},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user by firstname: {error}")
            raise map_database_error(error)

    def read_by_lastname(self, lastname: str) -> Optional[User]:
        """
        Read a user by lastname.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserByLastname",
                    params={"Lastname": lastname},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user by lastname: {error}")
            raise map_database_error(error)

    def update_by_id(self, user: User) -> Optional[User]:
        """
        Update a user by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateUserById",
                    params={
                        "Id": user.id,
                        "RowVersion": user.row_version_bytes,
                        "Firstname": user.firstname,
                        "Lastname": user.lastname,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update user by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[User]:
        """
        Delete a user by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteUserById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete user by ID: {error}")
            raise map_database_error(error)
