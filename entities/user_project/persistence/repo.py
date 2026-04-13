# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.user_project.business.model import UserProject
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class UserProjectRepository:
    """
    Repository for UserProject persistence operations.
    """

    def __init__(self):
        """Initialize the UserProjectRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[UserProject]:
        """
        Convert a database row into a UserProject dataclass.
        """
        if not row:
            return None

        try:
            return UserProject(
                id=row.Id,
                public_id=row.PublicId,
                row_version=base64.b64encode(row.RowVersion).decode("ascii"),
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                user_id=row.UserId,
                project_id=row.ProjectId,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during user project mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during user project mapping: {error}")
            raise map_database_error(error)

    def create(self, *, user_id: int, project_id: int) -> UserProject:
        """
        Create a new user project.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateUserProject",
                    params={
                        "UserId": user_id,
                        "ProjectId": project_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateUserProject did not return a row.")
                    raise map_database_error(Exception("CreateUserProject failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during create user project: {error}")
            raise map_database_error(error)

    def read_all(self) -> list[UserProject]:
        """
        Read all user projects.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserProjects",
                    params={}
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read all user projects: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[UserProject]:
        """
        Read a user project by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserProjectById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user project by ID: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[UserProject]:
        """
        Read a user project by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserProjectByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during read user project by public ID: {error}")
            raise map_database_error(error)

    def read_by_user_id(self, user_id: int) -> list[UserProject]:
        """
        Read user projects by user ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserProjectByUserId",
                    params={"UserId": user_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read user project by user ID: {error}")
            raise map_database_error(error)

    def read_by_project_id(self, project_id: int) -> list[UserProject]:
        """
        Read user projects by project ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadUserProjectByProjectId",
                    params={"ProjectId": project_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read user project by project ID: {error}")
            raise map_database_error(error)

    def update_by_id(self, user_project: UserProject) -> Optional[UserProject]:
        """
        Update a user project by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateUserProjectById",
                    params={
                        "Id": user_project.id,
                        "RowVersion": user_project.row_version_bytes,
                        "UserId": user_project.user_id,
                        "ProjectId": user_project.project_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error(f"Error during update user project by ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[UserProject]:
        """
        Delete a user project by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteUserProjectById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
        except Exception as error:
            logger.error(f"Error during delete user project by ID: {error}")
            raise map_database_error(error)
