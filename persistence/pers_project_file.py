"""
Module for project file.
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
class ProjectFile:
    """Represents a project file in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    project_id: Optional[int] = None
    module: Optional[str] = None
    path: Optional[str] = None
    transaction_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> 'ProjectFile':
        """Creates a Project instance from a database row."""
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            project_id=getattr(row, 'ProjectId', None),
            module=getattr(row, 'Module', None),
            path=getattr(row, 'Path', None),
            transaction_id=getattr(row, 'TransactionId', None)
        )


def create_project_file(project_file: ProjectFile) -> PersistenceResponse:
    """
    Creates a new project file in the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateProjectFile(?, ?, ?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    project_file.created_datetime,
                    project_file.modified_datetime,
                    project_file.project_id,
                    project_file.module,
                    project_file.path,
                    project_file.transaction_id
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Project file created successfully",
                        status_code=201,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Project file not created",
                        status_code=400,
                        success=False,
                        timestamp=datetime.now()
                    )
        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create project file: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_project_files() -> PersistenceResponse:
    """
    Retrieves all project files from the database.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadProjectFiles}"
                rows = cursor.execute(sql).fetchall()

                if rows:
                    return PersistenceResponse(
                        data=[ProjectFile.from_db_row(row) for row in rows],
                        message="Project files found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No project files found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read project files: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_buildone_project_file_by_guid(guid: str) -> PersistenceResponse:
    """
    Retrieves a project file from the database by GUID.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadProjectFileByGUID(?)}"
                row = cursor.execute(sql, guid).fetchone()
                if row:
                    return PersistenceResponse(
                        data=ProjectFile.from_db_row(row),
                        message="Project file found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Project file by guid not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read project file by guid: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_project_files_by_project_id(project_id: int) -> PersistenceResponse:
    """
    Retrieves all project files from the database by project id.
    """
    with pers_database.get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadProjectFilesByProjectId(?)}"
                rows = cursor.execute(sql, project_id).fetchall()

                if rows:
                    return PersistenceResponse(
                        data=[ProjectFile.from_db_row(row) for row in rows],
                        message="Project files found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No project by project id files found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read project files by project id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
