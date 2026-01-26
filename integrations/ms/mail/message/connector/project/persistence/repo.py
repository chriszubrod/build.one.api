# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.ms.mail.message.connector.project.business.model import MsMessageProject
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class MsMessageProjectRepository:
    """
    Repository for MsMessageProject persistence operations.
    """

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[MsMessageProject]:
        if not row:
            return None

        try:
            return MsMessageProject(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if row.RowVersion else None,
                created_datetime=str(getattr(row, "CreatedDatetime", None)) if getattr(row, "CreatedDatetime", None) else None,
                modified_datetime=str(getattr(row, "ModifiedDatetime", None)) if getattr(row, "ModifiedDatetime", None) else None,
                ms_message_id=getattr(row, "MsMessageId", None),
                project_id=getattr(row, "ProjectId", None),
                notes=getattr(row, "Notes", None),
            )
        except Exception as error:
            logger.error("Error during MsMessageProject mapping: %s", error)
            raise map_database_error(error)

    def create(self, *, ms_message_id: int, project_id: int, notes: Optional[str] = None) -> MsMessageProject:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateMsMessageProject",
                    params={"MsMessageId": ms_message_id, "ProjectId": project_id, "Notes": notes},
                )
                row = cursor.fetchone()
                if not row:
                    raise map_database_error(Exception("create MsMessageProject failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during create MsMessageProject: %s", error)
            raise map_database_error(error)

    def read_all(self) -> list[MsMessageProject]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadMsMessageProjects", params={})
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read all MsMessageProjects: %s", error)
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[MsMessageProject]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadMsMessageProjectByPublicId", params={"PublicId": public_id})
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read MsMessageProject by public ID: %s", error)
            raise map_database_error(error)

    def read_by_ms_message_id(self, ms_message_id: int) -> list[MsMessageProject]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadMsMessageProjectsByMsMessageId", params={"MsMessageId": ms_message_id})
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read MsMessageProjects by message ID: %s", error)
            raise map_database_error(error)

    def read_by_project_id(self, project_id: int) -> list[MsMessageProject]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="ReadMsMessageProjectsByProjectId", params={"ProjectId": project_id})
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read MsMessageProjects by project ID: %s", error)
            raise map_database_error(error)

    def update_by_public_id(self, *, public_id: str, notes: Optional[str] = None) -> Optional[MsMessageProject]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="UpdateMsMessageProjectByPublicId", params={"PublicId": public_id, "Notes": notes})
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during update MsMessageProject: %s", error)
            raise map_database_error(error)

    def delete_by_public_id(self, public_id: str) -> Optional[MsMessageProject]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(cursor=cursor, name="DeleteMsMessageProjectByPublicId", params={"PublicId": public_id})
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during delete MsMessageProject: %s", error)
            raise map_database_error(error)
