# Python Standard Library Imports
import base64
import logging
from datetime import datetime
from typing import List, Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.box.file.business.model import BoxFile, BoxPushLog
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class BoxFileRepository:
    """Persistence for `[box].[File]`. Calls sprocs in box file sql."""

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[BoxFile]:
        if not row:
            return None
        try:
            return BoxFile(
                id=getattr(row, "Id", None),
                public_id=str(row.PublicId) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if getattr(row, "RowVersion", None) else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                box_file_id=getattr(row, "BoxFileId", None),
                box_folder_id=getattr(row, "BoxFolderId", None),
                name=getattr(row, "Name", None),
                kind=getattr(row, "Kind", None),
                entity_type=getattr(row, "EntityType", None),
                entity_public_id=str(row.EntityPublicId) if getattr(row, "EntityPublicId", None) else None,
                attachment_id=getattr(row, "AttachmentId", None),
                project_id=getattr(row, "ProjectId", None),
                sha1=getattr(row, "Sha1", None),
                etag=getattr(row, "Etag", None),
                file_version_id=getattr(row, "FileVersionId", None),
                last_pushed_at=getattr(row, "LastPushedAt", None),
            )
        except Exception as error:
            logger.error(f"Error mapping BoxFile row: {error}")
            raise map_database_error(error)

    # --- Upsert ---

    def upsert(
        self,
        *,
        box_file_id: str,
        box_folder_id: str,
        name: str,
        kind: Optional[str] = None,
        entity_type: Optional[str] = None,
        entity_public_id: Optional[str] = None,
        attachment_id: Optional[int] = None,
        project_id: Optional[int] = None,
        sha1: Optional[str] = None,
        etag: Optional[str] = None,
        file_version_id: Optional[str] = None,
        last_pushed_at: Optional[datetime] = None,
    ) -> BoxFile:
        """MERGE on BoxFileId — create or refresh the registry row."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="UpsertBoxFile",
                        params={
                            "BoxFileId": box_file_id,
                            "BoxFolderId": box_folder_id,
                            "Name": name,
                            "Kind": kind,
                            "EntityType": entity_type,
                            "EntityPublicId": entity_public_id,
                            "AttachmentId": attachment_id,
                            "ProjectId": project_id,
                            "Sha1": sha1,
                            "Etag": etag,
                            "FileVersionId": file_version_id,
                            "LastPushedAt": last_pushed_at,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise map_database_error(Exception("upsert box file failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during upsert box file: {error}")
            raise map_database_error(error)

    # --- Read ---

    def read_by_box_file_id(self, box_file_id: str) -> Optional[BoxFile]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBoxFileByBoxFileId",
                        params={"BoxFileId": box_file_id},
                    )
                    return self._from_db(cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read box file by box file id: {error}")
            raise map_database_error(error)

    def read_by_entity(self, entity_type: str, entity_public_id: str) -> List[BoxFile]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBoxFilesByEntity",
                        params={
                            "EntityType": entity_type,
                            "EntityPublicId": entity_public_id,
                        },
                    )
                    return [self._from_db(row) for row in cursor.fetchall()]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read box files by entity: {error}")
            raise map_database_error(error)


class BoxPushLogRepository:
    """Persistence for `[box].[PushLog]` — append-only push audit trail."""

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[BoxPushLog]:
        if not row:
            return None
        try:
            return BoxPushLog(
                id=getattr(row, "Id", None),
                public_id=str(row.PublicId) if getattr(row, "PublicId", None) else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                box_file_id=getattr(row, "BoxFileId", None),
                file_version_id=getattr(row, "FileVersionId", None),
                sha1=getattr(row, "Sha1", None),
                request_id=str(row.RequestId) if getattr(row, "RequestId", None) else None,
                outbox_id=getattr(row, "OutboxId", None),
                actor_user_id=getattr(row, "ActorUserId", None),
            )
        except Exception as error:
            logger.error(f"Error mapping BoxPushLog row: {error}")
            raise map_database_error(error)

    # --- Create ---

    def create(
        self,
        *,
        box_file_id: str,
        file_version_id: Optional[str] = None,
        sha1: Optional[str] = None,
        request_id: Optional[str] = None,
        outbox_id: Optional[int] = None,
        actor_user_id: Optional[int] = None,
    ) -> BoxPushLog:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateBoxPushLog",
                        params={
                            "BoxFileId": box_file_id,
                            "FileVersionId": file_version_id,
                            "Sha1": sha1,
                            "RequestId": request_id,
                            "OutboxId": outbox_id,
                            "ActorUserId": actor_user_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise map_database_error(Exception("create box push log failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create box push log: {error}")
            raise map_database_error(error)

    # --- Read ---

    def read_by_box_file_id(self, box_file_id: str) -> List[BoxPushLog]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBoxPushLogsByBoxFileId",
                        params={"BoxFileId": box_file_id},
                    )
                    return [self._from_db(row) for row in cursor.fetchall()]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read box push logs by box file id: {error}")
            raise map_database_error(error)
