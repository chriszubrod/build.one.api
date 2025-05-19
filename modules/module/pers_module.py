"""
Module for module persistence.
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
class Module:
    """Represents a module in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    name: Optional[str] = None
    description: Optional[str] = None
    slug: Optional[str] = None
    transaction_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> Optional['Module']:
        """Creates a Module instance from a database row."""
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            name=getattr(row, 'Name', None),
            description=getattr(row, 'Desc', None),
            slug=getattr(row, 'Slug', None),
            transaction_id=getattr(row, 'TransactionId', None)
        )


def create_module(module: Module) -> PersistenceResponse:
    """
    Creates a new module in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateModule(?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    module.created_datetime,
                    module.modified_datetime,
                    module.name,
                    module.description,
                    module.slug
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Module created successfully",
                        status_code=201,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Module not created",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create module: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_modules() -> PersistenceResponse:
    """
    Reads all modules from the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadModules}"
                rows = cursor.execute(sql).fetchall()
                if rows:
                    return PersistenceResponse(
                        data=[Module.from_db_row(row) for row in rows],
                        message="Modules read successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No modules found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read modules: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_module_by_guid(module_guid: str) -> PersistenceResponse:
    """
    Reads a module from the database by its GUID.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadModuleByGUID(?)}"
                row = cursor.execute(sql, module_guid).fetchone()
                if row:
                    return PersistenceResponse(
                        data=Module.from_db_row(row),
                        message="Module read successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Module by guid not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read module by GUID: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_module_by_name(name: str) -> PersistenceResponse:
    """
    Retrieves a module from the database by name.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadModuleByName(?)}"
                row = cursor.execute(sql, name).fetchone()
                if row:
                    return PersistenceResponse(
                        data=Module.from_db_row(row),
                        message="Module read successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Module by name not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read module by name: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_module_by_slug(slug: str) -> PersistenceResponse:
    """
    Retrieves a module from the database by slug.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadModuleBySlug(?)}"
                row = cursor.execute(sql, slug).fetchone()
                if row:
                    return PersistenceResponse(
                        data=Module.from_db_row(row),
                        message="Module read successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Module by slug not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read module by slug: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_module(module: Module) -> PersistenceResponse:
    """
    Updates a module in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateModuleById(?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    module.id,
                    module.modified_datetime,
                    module.name,
                    module.description,
                    module.slug
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Module updated successfully",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Module not updated",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to update module: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )

