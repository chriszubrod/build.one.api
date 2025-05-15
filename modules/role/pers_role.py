"""
Module for role.
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
class Role:
    """Represents a role in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    name: Optional[str] = None
    transaction_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> 'Role':
        """Creates a Role instance from a database row."""
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            name=getattr(row, 'Name', None),
            transaction_id=getattr(row, 'TransactionId', None)
        )


def create_role(role: Role) -> PersistenceResponse:
    """
    Creates a new role in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateRole(?)}"
                rowcount = cursor.execute(sql, role.name).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Role created successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Failed to create role",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create role: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_roles() -> PersistenceResponse:
    """
    Retrieves all roles from the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadRoles()}"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[Role.from_db_row(row) for row in rows],
                        message="Roles retrieved successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No roles found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read roles: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_role_by_name(name: str) -> PersistenceResponse:
    """
    Retrieves a role from the database by name.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadRoleByName(?)}"
                row = cursor.execute(sql, name).fetchone()
                if row:
                    return PersistenceResponse(
                        data=Role.from_db_row(row),
                        message="Role retrieved successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Role not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read role by name: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_role_by_id(role_id: int) -> PersistenceResponse:
    """
    Retrieves a role from the database by id.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadRoleById(?)}"
                row = cursor.execute(sql, role_id).fetchone()
                if row:
                    return PersistenceResponse(
                        data=Role.from_db_row(row),
                        message="Role retrieved successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Role not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read role by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_role_by_guid(guid: str) -> PersistenceResponse:
    """
    Retrieves a role from the database by guid.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadRoleByGuid(?)}"
                row = cursor.execute(sql, guid).fetchone()
                if row:
                    return PersistenceResponse(
                        data=Role.from_db_row(row),
                        message="Role retrieved successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Role not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read role by guid: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_role_by_id(role: Role) -> PersistenceResponse:
    """
    Updates a role in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        print(role)
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateRoleById(?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    role.id,
                    role.guid,
                    role.created_datetime,
                    role.modified_datetime,
                    role.name,
                    role.transaction_id
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Role updated successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Failed to update role by id",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to update role by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
