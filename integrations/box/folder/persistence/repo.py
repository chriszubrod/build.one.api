# Python Standard Library Imports
import base64
import logging
from typing import List, Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.box.folder.business.model import BoxFolder, BoxProjectFolder
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class BoxFolderRepository:
    """Persistence for `[box].[Folder]`."""

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[BoxFolder]:
        if not row:
            return None
        try:
            return BoxFolder(
                id=getattr(row, "Id", None),
                public_id=str(row.PublicId) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if getattr(row, "RowVersion", None) else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                box_folder_id=getattr(row, "BoxFolderId", None),
                name=getattr(row, "Name", None),
                parent_box_folder_id=getattr(row, "ParentBoxFolderId", None),
            )
        except Exception as error:
            logger.error(f"Error mapping BoxFolder row: {error}")
            raise map_database_error(error)

    # --- Create ---

    def create(
        self,
        *,
        box_folder_id: str,
        name: str,
        parent_box_folder_id: Optional[str] = None,
    ) -> BoxFolder:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateBoxFolder",
                        params={
                            "BoxFolderId": box_folder_id,
                            "Name": name,
                            "ParentBoxFolderId": parent_box_folder_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise map_database_error(Exception("create box folder failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create box folder: {error}")
            raise map_database_error(error)

    # --- Read ---

    def read_by_id(self, id: int) -> Optional[BoxFolder]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(cursor=cursor, name="ReadBoxFolderById", params={"Id": id})
                    return self._from_db(cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read box folder by id: {error}")
            raise map_database_error(error)

    def read_by_box_folder_id(self, box_folder_id: str) -> Optional[BoxFolder]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBoxFolderByBoxFolderId",
                        params={"BoxFolderId": box_folder_id},
                    )
                    return self._from_db(cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read box folder by box folder id: {error}")
            raise map_database_error(error)


class BoxProjectFolderRepository:
    """Persistence for `[box].[ProjectFolder]` (joined reads include Folder /
    Project columns, so reads return dicts, not bare dataclasses)."""

    def __init__(self):
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[BoxProjectFolder]:
        if not row:
            return None
        try:
            return BoxProjectFolder(
                id=getattr(row, "Id", None),
                public_id=str(row.PublicId) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if getattr(row, "RowVersion", None) else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                project_id=getattr(row, "ProjectId", None),
                # Sprocs alias the BIGINT FK as [FolderId]; the name
                # [BoxFolderId] is reserved for Box's STRING id in every
                # result set (qbo/dbo keyspace lesson).
                box_folder_id=getattr(row, "FolderId", None),
                doc_class=getattr(row, "DocClass", None),
                created_by_user_id=getattr(row, "CreatedByUserId", None),
            )
        except Exception as error:
            logger.error(f"Error mapping BoxProjectFolder row: {error}")
            raise map_database_error(error)

    @staticmethod
    def _joined_row_to_dict(cursor: pyodbc.Cursor, row) -> Optional[dict]:
        """
        Map a ProjectFolder ⋈ Folder (⋈ Project) row to a mapping dict.

        Column-name defensive: `[box].[ProjectFolder].BoxFolderId` (BIGINT FK)
        collides with `[box].[Folder].BoxFolderId` (Box's string id), so the
        sproc must alias one of them — accept the common alias shapes, and
        when the names truly duplicate, `dict(zip(...))` keeps the LAST
        occurrence (the joined Folder column in pf.*, f.* select order).
        """
        if not row:
            return None
        columns = [c[0] for c in cursor.description]
        record = dict(zip(columns, row))

        external_folder_id = (
            record.get("FolderBoxFolderId")
            or record.get("BoxFolderExternalId")
            or record.get("BoxFolderId")
        )
        folder_name = record.get("FolderName") or record.get("Name")
        row_version = record.get("RowVersion")

        return {
            "id": record.get("Id"),
            "public_id": str(record["PublicId"]) if record.get("PublicId") else None,
            "row_version": base64.b64encode(row_version).decode("ascii") if row_version else None,
            "project_id": record.get("ProjectId"),
            "project_name": record.get("ProjectName"),
            "box_folder_id": str(external_folder_id) if external_folder_id is not None else None,
            "folder_name": folder_name,
            "doc_class": record.get("DocClass"),
            "created_by_user_id": record.get("CreatedByUserId"),
            "created_datetime": record.get("CreatedDatetime"),
            "modified_datetime": record.get("ModifiedDatetime"),
        }

    # --- Create ---

    def create(
        self,
        *,
        project_id: int,
        box_folder_id: int,
        doc_class: str = "invoices",
        created_by_user_id: Optional[int] = None,
    ) -> BoxProjectFolder:
        """`box_folder_id` is the local `[box].[Folder].Id` BIGINT, not the Box string id."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateBoxProjectFolder",
                        params={
                            "ProjectId": project_id,
                            "BoxFolderId": box_folder_id,
                            "DocClass": doc_class,
                            "CreatedByUserId": created_by_user_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise map_database_error(Exception("create box project folder failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create box project folder: {error}")
            raise map_database_error(error)

    # --- Read ---

    def read_by_project_id(self, project_id: int) -> Optional[dict]:
        """
        Joined read: the project's mapping row + the Folder's Box string id +
        name. A project may now have multiple rows (one per DocClass); the
        sproc orders 'invoices' first, so this legacy fetchone() returns the AP
        folder. Prefer `read_by_project_id_and_doc_class` for routing.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBoxProjectFolderByProjectId",
                        params={"ProjectId": project_id},
                    )
                    return self._joined_row_to_dict(cursor, cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read box project folder by project id: {error}")
            raise map_database_error(error)

    def read_by_project_id_and_doc_class(
        self, project_id: int, doc_class: str
    ) -> Optional[dict]:
        """Joined read for one (project, doc_class) — the routing-aware lookup."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBoxProjectFolderByProjectIdAndDocClass",
                        params={"ProjectId": project_id, "DocClass": doc_class},
                    )
                    return self._joined_row_to_dict(cursor, cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(
                f"Error during read box project folder by project id + doc class: {error}"
            )
            raise map_database_error(error)

    def read_all(self) -> List[dict]:
        """All mappings joined to Folder + dbo.Project.Name."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(cursor=cursor, name="ReadBoxProjectFolders", params={})
                    return [
                        self._joined_row_to_dict(cursor, row)
                        for row in cursor.fetchall()
                    ]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read box project folders: {error}")
            raise map_database_error(error)

    # --- Delete ---

    def delete_by_id(self, id: int, row_version: str) -> None:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteBoxProjectFolderById",
                        params={
                            "Id": id,
                            "RowVersion": base64.b64decode(row_version),
                        },
                    )
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during delete box project folder: {error}")
            raise map_database_error(error)
