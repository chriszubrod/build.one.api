"""
Module for Microsoft 365 auth.
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
class MsAuth:
    """Represents a Microsoft 365 auth in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    client_id: Optional[str] = None
    tenant: Optional[str] = None
    client_secret: Optional[str] = None
    access_token: Optional[str] = None
    expires_in: Optional[int] = None
    ext_expires_in: Optional[int] = None
    refresh_token: Optional[str] = None
    scope: Optional[str] = None
    token_type: Optional[str] = None
    user_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> Optional['MsAuth']:
        """
        Creates a MsAuth instance from a database row.
        """
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            client_id=getattr(row, 'ClientId', None),
            tenant=getattr(row, 'Tenant', None),
            client_secret=getattr(row, 'ClientSecret', None),
            access_token=getattr(row, 'AccessToken', None),
            expires_in=getattr(row, 'ExpiresIn', None),
            ext_expires_in=getattr(row, 'ExtExpiresIn', None),
            refresh_token=getattr(row, 'RefreshToken', None),
            scope=getattr(row, 'Scope', None),
            token_type=getattr(row, 'TokenType', None),
            user_id=getattr(row, 'UserId', None)
        )


def create_ms_auth(ms_auth: MsAuth) -> PersistenceResponse:
    """
    Creates a new Microsoft 365 auth in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateMsAuth(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    ms_auth.created_datetime,
                    ms_auth.modified_datetime,
                    ms_auth.client_id,
                    ms_auth.tenant,
                    ms_auth.client_secret,
                    ms_auth.access_token,
                    ms_auth.expires_in,
                    ms_auth.ext_expires_in,
                    ms_auth.refresh_token,
                    ms_auth.scope,
                    ms_auth.token_type,
                    ms_auth.user_id
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Microsoft 365 auth created successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Failed to create Microsoft 365 auth",
                        status_code=500,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create Microsoft 365 auth: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_ms_auths() -> PersistenceResponse:
    """
    Retrieves all Microsoft 365 auths from the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMsAuths()}"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[MsAuth.from_db_row(row) for row in rows],
                        message="Microsoft 365 auths read successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No Microsoft 365 auths found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Microsoft 365 auths: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_ms_auth_by_user_id(user_id) -> PersistenceResponse:
    """
    Read the Microsoft 365 auth by user id.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMsAuthByUserId(?)}"
                row = cursor.execute(sql, user_id).fetchone()
                if row:
                    return PersistenceResponse(
                        data=MsAuth.from_db_row(row),
                        message="Microsoft 365 auth found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No Microsoft 365 auth found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Microsoft 365 auth by user id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_ms_auth_by_guid(guid: str) -> PersistenceResponse:
    """
    Retrieves a Microsoft 365 auth from the database by GUID.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadMsAuthByGuid(?)}"
                row = cursor.execute(sql, guid).fetchone()
                if row:
                    return PersistenceResponse(
                        data=MsAuth.from_db_row(row),
                        message="Microsoft 365 auth found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Microsoft 365 auth not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read Microsoft 365 auth by guid: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_ms_auth_by_id(ms_auth: MsAuth) -> PersistenceResponse:
    """
    Updates a Microsoft 365 auth in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateMsAuthById(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    ms_auth.id,
                    ms_auth.guid,
                    ms_auth.created_datetime,
                    ms_auth.modified_datetime,
                    ms_auth.client_id,
                    ms_auth.tenant,
                    ms_auth.client_secret,
                    ms_auth.access_token,
                    ms_auth.expires_in,
                    ms_auth.ext_expires_in,
                    ms_auth.refresh_token,
                    ms_auth.scope,
                    ms_auth.token_type,
                    ms_auth.user_id
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Microsoft 365 auth updated successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Failed to update Microsoft 365 auth",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except pyodbc.Error as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to update Microsoft 365 auth: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
