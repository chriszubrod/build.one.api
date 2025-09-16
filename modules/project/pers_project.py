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
from shared.database import get_db_connection
from shared.response import PersistenceResponse


@dataclass
class Project:
    """Represents a project in the system."""
    id: Optional[int] = None
    guid: Optional[str] = None
    created_datetime: Optional[datetime] = None
    modified_datetime: Optional[datetime] = None
    name: Optional[str] = None
    abbreviation: Optional[str] = None
    status: Optional[str] = None
    customer_id: Optional[int] = None
    transaction_id: Optional[int] = None
    map_project_intuit_customer_id: Optional[int] = None
    intuit_customer_id: Optional[int] = None

    @classmethod
    def from_db_row(cls, row) -> Optional['Project']:
        """Creates a Project instance from a database row."""
        return cls(
            id=getattr(row, 'Id', None),
            guid=getattr(row, 'GUID', None),
            created_datetime=getattr(row, 'CreatedDatetime', None),
            modified_datetime=getattr(row, 'ModifiedDatetime', None),
            name=getattr(row, 'Name', None),
            abbreviation=getattr(row, 'Abbreviation', None),
            status=getattr(row, 'Status', None),
            customer_id=getattr(row, 'CustomerId', None),
            transaction_id=getattr(row, 'TransactionId', None),
            map_project_intuit_customer_id=getattr(row, 'ProjectIntuitCustomerId', None),
            intuit_customer_id=getattr(row, 'IntuitCustomerId', None)
        )


def create_project(project: Project) -> PersistenceResponse:
    """
    Creates a new project in the database.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL CreateProject(?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    project.name,
                    project.abbreviation,
                    project.status,
                    project.customer_id
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Project created",
                        status_code=201,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Failed to create project",
                        status_code=500,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to create project: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_projects() -> PersistenceResponse:
    """
    Retrieves all projects from the database.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadProjects}"
                rows = cursor.execute(sql).fetchall()

                if rows:
                    return PersistenceResponse(
                        data=[Project.from_db_row(row) for row in rows],
                        message="Projects found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No projects found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read projects: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_project_by_id(id: int) -> PersistenceResponse:
    """
    Retrieves a project from the database by ID.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadProjectByID(?)}"
                row = cursor.execute(sql, id).fetchone()

                if row:
                    return PersistenceResponse(
                        data=Project.from_db_row(row),
                        message="Project found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Project not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read project by guid: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_projects_by_customer_id(customer_id: int) -> PersistenceResponse:
    """
    Retrieves projects from the database by customer ID.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadProjectsByCustomerId(?)}"
                rows = cursor.execute(sql, customer_id).fetchall()

                if rows:
                    return PersistenceResponse(
                        data=[Project.from_db_row(row) for row in rows],
                        message="Projects found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="No projects found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read projects by customer id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_project_by_guid(guid: str) -> PersistenceResponse:
    """
    Retrieves a project from the database by GUID.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadProjectByGUID(?)}"
                row = cursor.execute(sql, guid).fetchone()

                if row:
                    return PersistenceResponse(
                        data=Project.from_db_row(row),
                        message="Project found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Project not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read project by guid: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def update_project_by_id(project: Project) -> PersistenceResponse:
    """
    Updates a project in the database by ID.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL UpdateProjectById(?, ?, ?, ?, ?, ?)}"
                rowcount = cursor.execute(
                    sql,
                    project.id,
                    project.modified_datetime,
                    project.name,
                    project.abbreviation,
                    project.status,
                    project.customer_id
                ).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Project updated",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Failed to update project by id",
                        status_code=500,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to update project by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def delete_project_by_id(id: int) -> PersistenceResponse:
    """
    Deletes a project in the database by ID.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL DeleteProjectById(?)}"
                rowcount = cursor.execute(sql, id).rowcount
                cnxn.commit()
                if rowcount > 0:
                    return PersistenceResponse(
                        data=rowcount,
                        message="Project deleted",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    cnxn.rollback()
                    return PersistenceResponse(
                        data=None,
                        message="Failed to delete project by id",
                        status_code=500,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            cnxn.rollback()
            return PersistenceResponse(
                data=None,
                message=f"Failed to delete project by id: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )


def read_project_intuit_customer_by_project_id(project_id: str) -> PersistenceResponse:
    """
    Retrieves a project from the database by GUID.
    """
    with get_db_connection() as cnxn:
        try:
            with cnxn.cursor() as cursor:
                sql = "{CALL ReadProjectIntuitCustomerByProjectID(?)}"
                row = cursor.execute(sql, project_id).fetchone()

                if row:
                    return PersistenceResponse(
                        data=Project.from_db_row(row),
                        message="Project found",
                        status_code=200,
                        success=True,
                        timestamp=datetime.now()
                    )
                else:
                    return PersistenceResponse(
                        data=None,
                        message="Project not found",
                        status_code=404,
                        success=False,
                        timestamp=datetime.now()
                    )

        except (pyodbc.Error) as e:
            return PersistenceResponse(
                data=None,
                message=f"Failed to read project by guid: {str(e)}",
                status_code=500,
                success=False,
                timestamp=datetime.now()
            )
