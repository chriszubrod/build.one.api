# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.ms.sharepoint.drive.business.model import MsDrive
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class MsDriveRepository:
    """
    Repository for MsDrive persistence operations.
    """

    def __init__(self):
        """Initialize the MsDriveRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[MsDrive]:
        """
        Convert a database row into a MsDrive dataclass.
        """
        if not row:
            return None

        try:
            return MsDrive(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if row.RowVersion else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                ms_site_id=getattr(row, "MsSiteId", None),
                drive_id=getattr(row, "DriveId", None),
                name=getattr(row, "Name", None),
                web_url=getattr(row, "WebUrl", None),
                drive_type=getattr(row, "DriveType", None),
            )
        except AttributeError as error:
            logger.error("Attribute error during ms drive mapping: %s", error)
            raise map_database_error(error)
        except Exception as error:
            logger.error("Unexpected error during ms drive mapping: %s", error)
            raise map_database_error(error)

    def create(
        self, *, ms_site_id: int, drive_id: str, name: str, web_url: str, drive_type: str
    ) -> MsDrive:
        """
        Create a new MsDrive.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateMsDrive",
                    params={
                        "MsSiteId": ms_site_id,
                        "DriveId": drive_id,
                        "Name": name,
                        "WebUrl": web_url,
                        "DriveType": drive_type,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Create ms drive did not return a row.")
                    raise map_database_error(Exception("create ms drive failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during create ms drive: %s", error)
            raise map_database_error(error)

    def read_all(self) -> list[MsDrive]:
        """
        Read all MsDrives.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsDrives",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read all ms drives: %s", error)
            raise map_database_error(error)

    def read_by_ms_site_id(self, ms_site_id: int) -> list[MsDrive]:
        """
        Read all MsDrives for a specific site.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsDrivesByMsSiteId",
                    params={
                        "MsSiteId": ms_site_id,
                    },
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read ms drives by site ID: %s", error)
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[MsDrive]:
        """
        Read a MsDrive by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsDriveByPublicId",
                    params={
                        "PublicId": public_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read ms drive by public ID: %s", error)
            raise map_database_error(error)

    def read_by_drive_id(self, drive_id: str) -> Optional[MsDrive]:
        """
        Read a MsDrive by MS Graph drive ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsDriveByDriveId",
                    params={
                        "DriveId": drive_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read ms drive by drive ID: %s", error)
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[MsDrive]:
        """
        Read a MsDrive by database ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsDriveById",
                    params={
                        "Id": id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read ms drive by ID: %s", error)
            raise map_database_error(error)

    def update_by_public_id(
        self,
        *,
        public_id: str,
        ms_site_id: int,
        drive_id: str,
        name: str,
        web_url: str,
        drive_type: str
    ) -> Optional[MsDrive]:
        """
        Update a MsDrive by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateMsDriveByPublicId",
                    params={
                        "PublicId": public_id,
                        "MsSiteId": ms_site_id,
                        "DriveId": drive_id,
                        "Name": name,
                        "WebUrl": web_url,
                        "DriveType": drive_type,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Update ms drive did not return a row.")
                    return None
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during update ms drive by public ID: %s", error)
            raise map_database_error(error)

    def delete_by_public_id(self, public_id: str) -> Optional[MsDrive]:
        """
        Delete a MsDrive by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteMsDriveByPublicId",
                    params={
                        "PublicId": public_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Delete ms drive did not return a row.")
                    return None
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during delete ms drive by public ID: %s", error)
            raise map_database_error(error)
