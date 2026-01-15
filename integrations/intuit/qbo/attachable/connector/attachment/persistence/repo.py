# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.intuit.qbo.attachable.connector.attachment.business.model import AttachableAttachment
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class AttachableAttachmentRepository:
    """
    Repository for AttachableAttachment persistence operations.
    """

    def __init__(self):
        """Initialize the AttachableAttachmentRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[AttachableAttachment]:
        """
        Convert a database row into an AttachableAttachment dataclass.
        """
        if not row:
            return None

        try:
            row_version_bytes = getattr(row, "RowVersion", None)
            return AttachableAttachment(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row_version_bytes).decode("ascii") if row_version_bytes else None,
                created_datetime=getattr(row, "CreatedDatetime", None),
                modified_datetime=getattr(row, "ModifiedDatetime", None),
                attachment_id=getattr(row, "AttachmentId", None),
                qbo_attachable_id=getattr(row, "QboAttachableId", None),
            )
        except AttributeError as error:
            logger.error(f"Attribute error during attachable attachment mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during attachable attachment mapping: {error}")
            raise map_database_error(error)

    def create(self, *, attachment_id: int, qbo_attachable_id: int) -> AttachableAttachment:
        """
        Create a new AttachableAttachment mapping record.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="CreateAttachableAttachment",
                        params={
                            "AttachmentId": attachment_id,
                            "QboAttachableId": qbo_attachable_id,
                        },
                    )
                    row = cursor.fetchone()
                    if not row:
                        logger.error("CreateAttachableAttachment did not return a row.")
                        raise map_database_error(Exception("CreateAttachableAttachment failed"))
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during create attachable attachment: {error}")
            raise map_database_error(error)

    def read_by_id(self, id: int) -> Optional[AttachableAttachment]:
        """
        Read an AttachableAttachment mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadAttachableAttachmentById",
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
            logger.error(f"Error during read attachable attachment by ID: {error}")
            raise map_database_error(error)

    def read_by_attachment_id(self, attachment_id: int) -> Optional[AttachableAttachment]:
        """
        Read an AttachableAttachment mapping record by Attachment ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadAttachableAttachmentByAttachmentId",
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
            logger.error(f"Error during read attachable attachment by attachment ID: {error}")
            raise map_database_error(error)

    def read_by_qbo_attachable_id(self, qbo_attachable_id: int) -> Optional[AttachableAttachment]:
        """
        Read an AttachableAttachment mapping record by QboAttachable ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadAttachableAttachmentByQboAttachableId",
                        params={"QboAttachableId": qbo_attachable_id},
                    )
                    row = cursor.fetchone()
                    return self._from_db(row)
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error during read attachable attachment by qbo attachable ID: {error}")
            raise map_database_error(error)

    def delete_by_id(self, id: int) -> Optional[AttachableAttachment]:
        """
        Delete an AttachableAttachment mapping record by ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="DeleteAttachableAttachmentById",
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
            logger.error(f"Error during delete attachable attachment by ID: {error}")
            raise map_database_error(error)
