# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from modules.project.business.model import Project
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class ProjectRepository:
    """
    Repository for Project persistence operations.
    """

    def __init__(self):
        """Initialize the ProjectRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[Project]:
        """
        Convert a database row into a Project dataclass.
        """
        if not row:
            return None

        try:
            return Project(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                name=row.Name,
                description=row.Description,
                status=row.Status,
                customer_id=row.CustomerId,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during project mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during project mapping: {error}")
            raise map_database_error(error)

    def create(self, *, name: str, description: str, status: str, customer_id: Optional[int] = None) -> Project:
        """
        Create a new project.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateProject",
                    params={
                        "Name": name,
                        "Description": description,
                        "Status": status,
                        "CustomerId": customer_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateProject did not return a row.")
                    raise map_database_error(Exception("CreateProject failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create project: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[Project]:
        """
        Read all projects.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadProjects",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all projects: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[Project]:
        """
        Read a project by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadProjectById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read project by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[Project]:
        """
        Read a project by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadProjectByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read project by public ID: {error}")
            raise map_database_error(error)

    def read_by_name(self, name: str) -> Optional[Project]:
        """
        Read a project by name.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadProjectByName",
                    params={"Name": name},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read project by name: {error}")
            raise map_database_error(error)

    def update_by_id(self, project: Project) -> Optional[Project]:
        """
        Update a project by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateProjectById",
                    params={
                        "Id": project.id,
                        "RowVersion": project.row_version_bytes,
                        "Name": project.name,
                        "Description": project.description,
                        "Status": project.status,
                        "CustomerId": project.customer_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update project by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[Project]:
        """
        Delete a project by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteProjectById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete project by ID: {error}")
            raise map_database_error(error)
