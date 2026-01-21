# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.ms.sharepoint.driveitem.connector.attachment.business.model import DriveItemAttachment
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class DriveItemAttachmentRepository:
    """
    Repository for DriveItemAttachment persistence operations.
    """

    def __init__(self):
        """Initialize the DriveItemAttachmentRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[DriveItemAttachment]:
        """
        Convert a database row into a DriveItemAttachment dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return DriveItemAttachment(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                attachment_id=getattr(row, "AttachmentId", None),
                ms_driveitem_id=getattr(row, "MsDriveItemId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during driveitem attachment mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during driveitem attachment mapping: {error}")
            raise map_database_error(error)

    def create(self, *, attachment_id: int, ms_driveitem_id: int) -> DriveItemAttachment:
        """
        Create a new DriveItemAttachment mapping record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateDriveItemAttachment",
                        params={
                            "AttachmentId": attachment_id,
                            "MsDriveItemId": ms_driveitem_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateDriveItemAttachment did not return a row.")
                        raise map_database_error(Exception("CreateDriveItemAttachment failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create driveitem attachment: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[DriveItemAttachment]:
        """
        Read a DriveItemAttachment mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadDriveItemAttachmentById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read driveitem attachment by ID: {error}")
            raise map_database_error(error)

    def read_by_attachment_id(self, attachment_id: int) -> Optional[DriveItemAttachment]:
        """
        Read a DriveItemAttachment mapping record by Attachment ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadDriveItemAttachmentByAttachmentId",
                        params={"AttachmentId": attachment_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read driveitem attachment by attachment ID: {error}")
            raise map_database_error(error)

    def read_by_ms_driveitem_id(self, ms_driveitem_id: int) -> Optional[DriveItemAttachment]:
        """
        Read a DriveItemAttachment mapping record by MS DriveItem ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadDriveItemAttachmentByMsDriveItemId",
                        params={"MsDriveItemId": ms_driveitem_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read driveitem attachment by ms driveitem ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[DriveItemAttachment]:
        """
        Delete a DriveItemAttachment mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteDriveItemAttachmentById",
                        params={"Id": id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during delete driveitem attachment by ID: {error}")
            raise map_database_error(error)

    def delete_by_attachment_id(self, attachment_id: int) -> Optional[DriveItemAttachment]:
        """
        Delete a DriveItemAttachment mapping record by Attachment ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteDriveItemAttachmentByAttachmentId",
                        params={"AttachmentId": attachment_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row) if row else None
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during delete driveitem attachment by attachment ID: {error}")
            raise map_database_error(error)
