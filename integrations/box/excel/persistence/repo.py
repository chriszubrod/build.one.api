# Python Standard Library Imports
import base64
import logging
from typing import List, Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.box.excel.business.model import BoxProjectWorkbook
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class BoxProjectWorkbookRepository:
    """
    Persistence for `[box].[ProjectWorkbook]`.

    The Box file id lives directly on the row (no separate registry table), so
    reads return a flat dict; list reads join dbo.Project for the admin
    surface. `box_file_id` is Box's STRING id — never aliased into a BIGINT.
    """

    def __init__(self):
        pass

    @staticmethod
    def _row_to_dict(cursor: pyodbc.Cursor, row) -> Optional[dict]:
        if not row:
            return None
        columns = [c[0] for c in cursor.description]
        record = dict(zip(columns, row))
        row_version = record.get("RowVersion")
        box_file_id = record.get("BoxFileId")
        return {
            "id": record.get("Id"),
            "public_id": str(record["PublicId"]) if record.get("PublicId") else None,
            "row_version": base64.b64encode(row_version).decode("ascii") if row_version else None,
            "project_id": record.get("ProjectId"),
            "project_name": record.get("ProjectName"),
            "project_public_id": str(record["ProjectPublicId"]) if record.get("ProjectPublicId") else None,
            # Box's STRING file id — always carried as str, never BIGINT.
            "box_file_id": str(box_file_id) if box_file_id is not None else None,
            "worksheet_name": record.get("WorksheetName"),
            "created_by_user_id": record.get("CreatedByUserId"),
            "created_datetime": record.get("CreatedDatetime"),
            "modified_datetime": record.get("ModifiedDatetime"),
        }

    # --- Create (upsert-shaped on ProjectId so re-map is idempotent) ---

    def create(
        self,
        *,
        project_id: int,
        box_file_id: str,
        worksheet_name: str = "DETAILS",
        created_by_user_id: Optional[int] = None,
    ) -> Optional[dict]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateBoxProjectWorkbook",
                        params={
                            "ProjectId": project_id,
                            "BoxFileId": box_file_id,
                            "WorksheetName": worksheet_name,
                            "CreatedByUserId": created_by_user_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise map_database_error(Exception("create box project workbook failed"))
                    return self._row_to_dict(cursor, row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create box project workbook: {error}")
            raise map_database_error(error)

    # --- Read ---

    def read_by_project_id(self, project_id: int) -> Optional[dict]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadBoxProjectWorkbookByProjectId",
                        params={"ProjectId": project_id},
                    )
                    return self._row_to_dict(cursor, cursor.fetchone())
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read box project workbook by project id: {error}")
            raise map_database_error(error)

    def read_all(self) -> List[dict]:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(cursor=cursor, name="ReadBoxProjectWorkbooks", params={})
                    return [
                        self._row_to_dict(cursor, row)
                        for row in cursor.fetchall()
                    ]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read box project workbooks: {error}")
            raise map_database_error(error)

    # --- Delete ---

    def delete_by_id(self, id: int, row_version: str) -> None:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteBoxProjectWorkbookById",
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
            logger.error(f"Error during delete box project workbook: {error}")
            raise map_database_error(error)
