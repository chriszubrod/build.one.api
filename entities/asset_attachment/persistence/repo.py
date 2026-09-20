# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from entities.asset_attachment.business.model import AssetAttachment
from shared.database import call_procedure, get_connection, map_database_error

logger = logging.getLogger(__name__)


class AssetAttachmentRepository:
    def _from_db(self, row: pyodbc.Row) -> Optional[AssetAttachment]:
        if not row:
            return None
        return AssetAttachment(
            id=row.Id,
            public_id=str(row.PublicId) if row.PublicId is not None else None,
            row_version=base64.b64encode(row.RowVersion).decode("ascii"),
            created_datetime=row.CreatedDatetime,
            modified_datetime=row.ModifiedDatetime,
            asset_id=row.AssetId,
            attachment_id=row.AttachmentId,
        )

    def create(self, *, asset_id: int, attachment_id: int) -> AssetAttachment:
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateAssetAttachment",
                        params={"AssetId": asset_id, "AttachmentId": attachment_id},
                    )
                    row = cursor.fetchone()
                    if not row:
                        raise map_database_error(Exception("CreateAssetAttachment failed"))
                    return self._from_db(row)
                finally:
                    cursor.close()
        except Exception as error:
            logger.error(f"Error during create asset attachment: {error}")
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[AssetAttachment]:
        with get_connection() as conn:
            cursor = conn.cursor()
            try:
                call_procedure(
                    cursor=cursor,
                    name="ReadAssetAttachmentByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._from_db(row)
            finally:
                cursor.close()

    def read_by_asset_id(self, asset_id: int) -> list[AssetAttachment]:
        with get_connection() as conn:
            cursor = conn.cursor()
            try:
                call_procedure(
                    cursor=cursor,
                    name="ReadAssetAttachmentsByAssetId",
                    params={"AssetId": asset_id},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
            finally:
                cursor.close()

    def delete_by_id(self, id: int) -> Optional[AssetAttachment]:
        with get_connection() as conn:
            cursor = conn.cursor()
            try:
                call_procedure(
                    cursor=cursor,
                    name="DeleteAssetAttachmentById",
                    params={"Id": id},
                )
                row = cursor.fetchone()
                return self._from_db(row) if row else None
            finally:
                cursor.close()

    def delete_by_asset_id(self, asset_id: int) -> None:
        with get_connection() as conn:
            cursor = conn.cursor()
            try:
                call_procedure(
                    cursor=cursor,
                    name="DeleteAssetAttachmentsByAssetId",
                    params={"AssetId": asset_id},
                )
            finally:
                cursor.close()
