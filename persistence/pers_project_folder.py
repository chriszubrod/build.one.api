"""
Module for project folder.
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
class ProjectFolder:
    """Represents a project folder in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    project_id: Optional[int] = None
    module: Optional[str] = None
    path: Optional[str] = None
    url: Optional[str] = None
    transaction_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> Optional['ProjectFolder']:
        """Creates a Project instance from a database row."""
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            project_id=getattr(row, 'ProjectId', None),
            module=getattr(row, 'Module', None),
            path=getattr(row, 'Path', None),
            url=getattr(row, 'Url', None),
            transaction_id=getattr(row, 'TransactionId', None)
        )


def create_project_folder(project_folder: ProjectFolder):
    """
    Creates a new project folder in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateProjectFolder(?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    project_folder.created_datetime,
                    project_folder.modified_datetime,
                    project_folder.project_id,
                    project_folder.module,
                    project_folder.path,
                    project_folder.url,
                    project_folder.transaction_id
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Project folder created",
                        status_code=201,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Project folder not created",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create project folder: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_project_folders() -> PersistenceResponse:
    """
    Retrieves all project folders from the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadProjectFolders}"
                rows = cursor.execute(sql).fetchall()

                if rows:
                    return PersistenceResponse(
                        data=[ProjectFolder.from_db_row(row) for row in rows],
                        message="Project folders found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No project folders found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read project folders: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_project_folder_by_guid(guid: str) -> PersistenceResponse:
    """
    Retrieves a project folder from the database by GUID.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadProjectFolderByGUID(?)}"
                row = cursor.execute(sql, guid).fetchone()

                if row:
                    return PersistenceResponse(
                        data=ProjectFolder.from_db_row(row),
                        message="Project folder found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Project folder by guid not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read project folder by guid: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_project_folders_by_project_id(project_id: int) -> PersistenceResponse:
    """
    Retrieves all project folders from the database by project id.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadProjectFoldersByProjectId(?)}"
                rows = cursor.execute(sql, project_id).fetchall()

                if rows:
                    return PersistenceResponse(
                        data=[ProjectFolder.from_db_row(row) for row in rows],
                        message="Project folders found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No project folders by project id found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read project folders by project id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_project_folders_by_project_id_by_module(project_id: int, module: str) -> PersistenceResponse:
    """
    Retrieves a project folder by project id and module from the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadProjectFolderByProjectIdByModule(?, ?)}"
                row = cursor.execute(sql, project_id, module).fetchone()

                if row:
                    return PersistenceResponse(
                        data=ProjectFolder.from_db_row(row),
                        message="Project folder found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Project folder by project id and module not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read project folder by project id and module: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
