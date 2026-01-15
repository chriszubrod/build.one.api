# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.ms.sharepoint.driveitem.business.model import MsDriveItem
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class MsDriveItemRepository:
    """
    Repository for MsDriveItem persistence operations.
    """

    def __init__(self):
        """Initialize the MsDriveItemRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[MsDriveItem]:
        """
        Convert a database row into a MsDriveItem dataclass.
        """
        if not row:
            return None

        try:
            return MsDriveItem(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if row.RowVersion else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                ms_drive_id=getattr(row, "MsDriveId", None),
                item_id=getattr(row, "ItemId", None),
                parent_item_id=getattr(row, "ParentItemId", None),
                name=getattr(row, "Name", None),
                item_type=getattr(row, "ItemType", None),
                size=getattr(row, "Size", None),
                mime_type=getattr(row, "MimeType", None),
                web_url=getattr(row, "WebUrl", None),
                graph_created_datetime=getattr(row, "GraphCreatedDatetime", None),
                graph_modified_datetime=getattr(row, "GraphModifiedDatetime", None),
            )
        except AttributeError as error:
            logger.error("Attribute error during ms drive item mapping: %s", error)
            raise map_database_error(error)
        except Exception as error:
            logger.error("Unexpected error during ms drive item mapping: %s", error)
            raise map_database_error(error)

    def create(
        self,
        *,
        ms_drive_id: int,
        item_id: str,
        parent_item_id: Optional[str],
        name: str,
        item_type: str,
        size: Optional[int],
        mime_type: Optional[str],
        web_url: str,
        graph_created_datetime: Optional[str],
        graph_modified_datetime: Optional[str]
    ) -> MsDriveItem:
        """
        Create a new MsDriveItem.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateMsDriveItem",
                    params={
                        "MsDriveId": ms_drive_id,
                        "ItemId": item_id,
                        "ParentItemId": parent_item_id,
                        "Name": name,
                        "ItemType": item_type,
                        "Size": size,
                        "MimeType": mime_type,
                        "WebUrl": web_url,
                        "GraphCreatedDatetime": graph_created_datetime,
                        "GraphModifiedDatetime": graph_modified_datetime,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Create ms drive item did not return a row.")
                    raise map_database_error(Exception("create ms drive item failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during create ms drive item: %s", error)
            raise map_database_error(error)

    def read_all(self) -> list[MsDriveItem]:
        """
        Read all MsDriveItems.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsDriveItems",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read all ms drive items: %s", error)
            raise map_database_error(error)

    def read_by_ms_drive_id(self, ms_drive_id: int) -> list[MsDriveItem]:
        """
        Read all MsDriveItems for a specific drive.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsDriveItemsByMsDriveId",
                    params={
                        "MsDriveId": ms_drive_id,
                    },
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read ms drive items by drive ID: %s", error)
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[MsDriveItem]:
        """
        Read a MsDriveItem by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsDriveItemByPublicId",
                    params={
                        "PublicId": public_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read ms drive item by public ID: %s", error)
            raise map_database_error(error)

    def read_by_item_id(self, item_id: str) -> Optional[MsDriveItem]:
        """
        Read a MsDriveItem by MS Graph item ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsDriveItemByItemId",
                    params={
                        "ItemId": item_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read ms drive item by item ID: %s", error)
            raise map_database_error(error)

    def read_by_parent_item_id(self, parent_item_id: str) -> list[MsDriveItem]:
        """
        Read all MsDriveItems with a specific parent.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsDriveItemsByParentItemId",
                    params={
                        "ParentItemId": parent_item_id,
                    },
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read ms drive items by parent item ID: %s", error)
            raise map_database_error(error)

    def update_by_public_id(
        self,
        *,
        public_id: str,
        ms_drive_id: int,
        item_id: str,
        parent_item_id: Optional[str],
        name: str,
        item_type: str,
        size: Optional[int],
        mime_type: Optional[str],
        web_url: str,
        graph_created_datetime: Optional[str],
        graph_modified_datetime: Optional[str]
    ) -> Optional[MsDriveItem]:
        """
        Update a MsDriveItem by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateMsDriveItemByPublicId",
                    params={
                        "PublicId": public_id,
                        "MsDriveId": ms_drive_id,
                        "ItemId": item_id,
                        "ParentItemId": parent_item_id,
                        "Name": name,
                        "ItemType": item_type,
                        "Size": size,
                        "MimeType": mime_type,
                        "WebUrl": web_url,
                        "GraphCreatedDatetime": graph_created_datetime,
                        "GraphModifiedDatetime": graph_modified_datetime,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Update ms drive item did not return a row.")
                    return None
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during update ms drive item by public ID: %s", error)
            raise map_database_error(error)

    def delete_by_public_id(self, public_id: str) -> Optional[MsDriveItem]:
        """
        Delete a MsDriveItem by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteMsDriveItemByPublicId",
                    params={
                        "PublicId": public_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Delete ms drive item did not return a row.")
                    return None
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during delete ms drive item by public ID: %s", error)
            raise map_database_error(error)
