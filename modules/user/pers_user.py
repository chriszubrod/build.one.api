"""
Module for user.
"""

# python standard library imports
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# third party imports
import pyodbc

# local imports
from persistence import pers_database
from persistence.pers_response import PersistenceResponse


@dataclass
class User:
    """Represents a user in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    username: Optional[str] = None
    password_hash: Optional[str] = None
    password_salt: Optional[str] = None
    is_active: Optional[bool] = None
    role_id: Optional[int] = None
    transaction_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> Optional['User']:
        """
        Creates a User instance from a database row.
        """
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            is_active=getattr(row, 'IsActive', None),
            role_id=getattr(row, 'RoleId', None),
            transaction_id=getattr(row, 'TransactionId', None),
            username=getattr(row, 'Username', None),
            password_hash=getattr(row, 'PasswordHash', None),
            password_salt=getattr(row, 'PasswordSalt', None),
        )


def create_user(user: User) -> PersistenceResponse:
    """
    Creates a new user in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateUser(?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    user.created_datetime,
                    user.modified_datetime,
                    user.is_active,
                    user.role_id,
                    user.username,
                    user.password_hash,
                    user.password_salt
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="User created successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Failed to create user",
                        status_code=500,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create user: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_users() -> PersistenceResponse:
    """
    Retrieves all users from the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadUsers()}"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[User.from_db_row(row) for row in rows],
                        message="Users read successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No users found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read users: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_user_by_guid(guid: str) -> PersistenceResponse:
    """
    Retrieves a user from the database by GUID.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadUserByGuid(?)}"
                row = cursor.execute(sql, guid).fetchone()
                if row:
                    return PersistenceResponse(
                        data=User.from_db_row(row),
                        message="User found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="User not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read user by guid: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_user_by_username(username: str) -> PersistenceResponse:
    """
    Retrieves a user from the database by username.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadUserByUsername(?)}"
                row = cursor.execute(sql, username).fetchone()
                if row:
                    return PersistenceResponse(
                        data=User.from_db_row(row),
                        message="Username found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Username not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"An error occurred while reading the user by username: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_user_by_id(user: User) -> PersistenceResponse:
    """
    Updates a user in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateUserById(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    user.id,
                    user.guid,
                    user.created_datetime,
                    user.modified_datetime,
                    user.is_active,
                    user.role_id,
                    user.transaction_id,
                    user.username,
                    user.password_hash,
                    user.password_salt
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="User updated successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Failed to update user",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except pyodbc.Error as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to update user: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
