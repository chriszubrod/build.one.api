# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Third-party Imports
import pyodbc

# Local Imports
from integrations.ms.mail.message.business.model import (
    MsMessage,
    MsMessageRecipient,
    MsMessageAttachment,
)
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class MsMessageRepository:
    """
    Repository for MsMessage persistence operations.
    """

    def __init__(self):
        """Initialize the MsMessageRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[MsMessage]:
        """
        Convert a database row into a MsMessage dataclass.
        """
        if not row:
            return None

        try:
            return MsMessage(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if row.RowVersion else None,
                created_datetime=str(getattr(row, "CreatedDatetime", None)) if getattr(row, "CreatedDatetime", None) else None,
                modified_datetime=str(getattr(row, "ModifiedDatetime", None)) if getattr(row, "ModifiedDatetime", None) else None,
                message_id=getattr(row, "MessageId", None),
                conversation_id=getattr(row, "ConversationId", None),
                internet_message_id=getattr(row, "InternetMessageId", None),
                subject=getattr(row, "Subject", None),
                from_email=getattr(row, "FromEmail", None),
                from_name=getattr(row, "FromName", None),
                received_datetime=str(getattr(row, "ReceivedDatetime", None)) if getattr(row, "ReceivedDatetime", None) else None,
                sent_datetime=str(getattr(row, "SentDatetime", None)) if getattr(row, "SentDatetime", None) else None,
                body_content_type=getattr(row, "BodyContentType", None),
                body=getattr(row, "Body", None),
                body_preview=getattr(row, "BodyPreview", None),
                is_read=getattr(row, "IsRead", None),
                has_attachments=getattr(row, "HasAttachments", None),
                importance=getattr(row, "Importance", None),
                web_link=getattr(row, "WebLink", None),
            )
        except AttributeError as error:
            logger.error("Attribute error during MsMessage mapping: %s", error)
            raise map_database_error(error)
        except Exception as error:
            logger.error("Unexpected error during MsMessage mapping: %s", error)
            raise map_database_error(error)

    def create(
        self,
        *,
        message_id: str,
        from_email: str,
        conversation_id: Optional[str] = None,
        internet_message_id: Optional[str] = None,
        subject: Optional[str] = None,
        from_name: Optional[str] = None,
        received_datetime: Optional[str] = None,
        sent_datetime: Optional[str] = None,
        body_content_type: str = "HTML",
        body: Optional[str] = None,
        body_preview: Optional[str] = None,
        is_read: bool = False,
        has_attachments: bool = False,
        importance: str = "normal",
        web_link: Optional[str] = None
    ) -> MsMessage:
        """
        Create a new linked MsMessage.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateMsMessage",
                    params={
                        "MessageId": message_id,
                        "ConversationId": conversation_id,
                        "InternetMessageId": internet_message_id,
                        "Subject": subject,
                        "FromEmail": from_email,
                        "FromName": from_name,
                        "ReceivedDatetime": received_datetime,
                        "SentDatetime": sent_datetime,
                        "BodyContentType": body_content_type,
                        "Body": body,
                        "BodyPreview": body_preview,
                        "IsRead": is_read,
                        "HasAttachments": has_attachments,
                        "Importance": importance,
                        "WebLink": web_link,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Create MsMessage did not return a row.")
                    raise map_database_error(Exception("create MsMessage failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during create MsMessage: %s", error)
            raise map_database_error(error)

    def read_all(self) -> list[MsMessage]:
        """
        Read all linked MsMessages.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsMessages",
                    params={},
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read all MsMessages: %s", error)
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[MsMessage]:
        """
        Read a MsMessage by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsMessageByPublicId",
                    params={
                        "PublicId": public_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read MsMessage by public ID: %s", error)
            raise map_database_error(error)

    def read_by_message_id(self, message_id: str) -> Optional[MsMessage]:
        """
        Read a MsMessage by Graph message ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsMessageByMessageId",
                    params={
                        "MessageId": message_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read MsMessage by message ID: %s", error)
            raise map_database_error(error)

    def read_by_conversation_id(self, conversation_id: str) -> list[MsMessage]:
        """
        Read all MsMessages in a conversation thread.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsMessagesByConversationId",
                    params={
                        "ConversationId": conversation_id,
                    },
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read MsMessages by conversation ID: %s", error)
            raise map_database_error(error)

    def read_by_from_email(self, from_email: str) -> list[MsMessage]:
        """
        Read all MsMessages from a specific sender.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsMessagesByFromEmail",
                    params={
                        "FromEmail": from_email,
                    },
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read MsMessages by from email: %s", error)
            raise map_database_error(error)

    def update_by_public_id(
        self,
        *,
        public_id: str,
        subject: Optional[str] = None,
        body_content_type: str = "HTML",
        body: Optional[str] = None,
        body_preview: Optional[str] = None,
        is_read: bool = False,
        has_attachments: bool = False,
        importance: str = "normal"
    ) -> Optional[MsMessage]:
        """
        Update a MsMessage by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateMsMessageByPublicId",
                    params={
                        "PublicId": public_id,
                        "Subject": subject,
                        "BodyContentType": body_content_type,
                        "Body": body,
                        "BodyPreview": body_preview,
                        "IsRead": is_read,
                        "HasAttachments": has_attachments,
                        "Importance": importance,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Update MsMessage did not return a row.")
                    return None
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during update MsMessage by public ID: %s", error)
            raise map_database_error(error)

    def delete_by_public_id(self, public_id: str) -> Optional[MsMessage]:
        """
        Delete a MsMessage by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteMsMessageByPublicId",
                    params={
                        "PublicId": public_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Delete MsMessage did not return a row.")
                    return None
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during delete MsMessage by public ID: %s", error)
            raise map_database_error(error)


class MsMessageRecipientRepository:
    """
    Repository for MsMessageRecipient persistence operations.
    """

    def __init__(self):
        """Initialize the MsMessageRecipientRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[MsMessageRecipient]:
        """
        Convert a database row into a MsMessageRecipient dataclass.
        """
        if not row:
            return None

        try:
            return MsMessageRecipient(
                id=getattr(row, "Id", None),
                ms_message_id=getattr(row, "MsMessageId", None),
                recipient_type=getattr(row, "RecipientType", None),
                email=getattr(row, "Email", None),
                name=getattr(row, "Name", None),
            )
        except AttributeError as error:
            logger.error("Attribute error during MsMessageRecipient mapping: %s", error)
            raise map_database_error(error)
        except Exception as error:
            logger.error("Unexpected error during MsMessageRecipient mapping: %s", error)
            raise map_database_error(error)

    def create(
        self,
        *,
        ms_message_id: int,
        recipient_type: str,
        email: str,
        name: Optional[str] = None
    ) -> MsMessageRecipient:
        """
        Create a new MsMessageRecipient.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateMsMessageRecipient",
                    params={
                        "MsMessageId": ms_message_id,
                        "RecipientType": recipient_type,
                        "Email": email,
                        "Name": name,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Create MsMessageRecipient did not return a row.")
                    raise map_database_error(Exception("create MsMessageRecipient failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during create MsMessageRecipient: %s", error)
            raise map_database_error(error)

    def read_by_ms_message_id(self, ms_message_id: int) -> list[MsMessageRecipient]:
        """
        Read all recipients for a message.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsMessageRecipientsByMsMessageId",
                    params={
                        "MsMessageId": ms_message_id,
                    },
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read MsMessageRecipients by message ID: %s", error)
            raise map_database_error(error)

    def delete_by_ms_message_id(self, ms_message_id: int) -> None:
        """
        Delete all recipients for a message.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteMsMessageRecipientsByMsMessageId",
                    params={
                        "MsMessageId": ms_message_id,
                    },
                )
        except Exception as error:
            logger.error("Error during delete MsMessageRecipients by message ID: %s", error)
            raise map_database_error(error)


class MsMessageAttachmentRepository:
    """
    Repository for MsMessageAttachment persistence operations.
    """

    def __init__(self):
        """Initialize the MsMessageAttachmentRepository."""
        pass

    def _from_db(self, row: pyodbc.Row) -> Optional[MsMessageAttachment]:
        """
        Convert a database row into a MsMessageAttachment dataclass.
        """
        if not row:
            return None

        try:
            return MsMessageAttachment(
                id=getattr(row, "Id", None),
                public_id=str(getattr(row, "PublicId", None)) if getattr(row, "PublicId", None) else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if row.RowVersion else None,
                created_datetime=str(getattr(row, "CreatedDatetime", None)) if getattr(row, "CreatedDatetime", None) else None,
                modified_datetime=str(getattr(row, "ModifiedDatetime", None)) if getattr(row, "ModifiedDatetime", None) else None,
                ms_message_id=getattr(row, "MsMessageId", None),
                attachment_id=getattr(row, "AttachmentId", None),
                name=getattr(row, "Name", None),
                content_type=getattr(row, "ContentType", None),
                size=getattr(row, "Size", None),
                is_inline=getattr(row, "IsInline", None),
                blob_url=getattr(row, "BlobUrl", None),
                blob_container=getattr(row, "BlobContainer", None),
                blob_name=getattr(row, "BlobName", None),
            )
        except AttributeError as error:
            logger.error("Attribute error during MsMessageAttachment mapping: %s", error)
            raise map_database_error(error)
        except Exception as error:
            logger.error("Unexpected error during MsMessageAttachment mapping: %s", error)
            raise map_database_error(error)

    def create(
        self,
        *,
        ms_message_id: int,
        attachment_id: str,
        name: str,
        content_type: str,
        size: Optional[int] = None,
        is_inline: bool = False,
        blob_url: Optional[str] = None,
        blob_container: Optional[str] = None,
        blob_name: Optional[str] = None
    ) -> MsMessageAttachment:
        """
        Create a new MsMessageAttachment.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="CreateMsMessageAttachment",
                    params={
                        "MsMessageId": ms_message_id,
                        "AttachmentId": attachment_id,
                        "Name": name,
                        "ContentType": content_type,
                        "Size": size,
                        "IsInline": is_inline,
                        "BlobUrl": blob_url,
                        "BlobContainer": blob_container,
                        "BlobName": blob_name,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Create MsMessageAttachment did not return a row.")
                    raise map_database_error(Exception("create MsMessageAttachment failed"))
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during create MsMessageAttachment: %s", error)
            raise map_database_error(error)

    def read_by_ms_message_id(self, ms_message_id: int) -> list[MsMessageAttachment]:
        """
        Read all attachments for a message.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsMessageAttachmentsByMsMessageId",
                    params={
                        "MsMessageId": ms_message_id,
                    },
                )
                rows = cursor.fetchall()
                return [self._from_db(row) for row in rows if row]
        except Exception as error:
            logger.error("Error during read MsMessageAttachments by message ID: %s", error)
            raise map_database_error(error)

    def read_by_public_id(self, public_id: str) -> Optional[MsMessageAttachment]:
        """
        Read a MsMessageAttachment by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadMsMessageAttachmentByPublicId",
                    params={
                        "PublicId": public_id,
                    },
                )
                row = cursor.fetchone()
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during read MsMessageAttachment by public ID: %s", error)
            raise map_database_error(error)

    def update_blob(
        self,
        *,
        public_id: str,
        blob_url: str,
        blob_container: str,
        blob_name: str
    ) -> Optional[MsMessageAttachment]:
        """
        Update blob storage info for an attachment.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="UpdateMsMessageAttachmentBlob",
                    params={
                        "PublicId": public_id,
                        "BlobUrl": blob_url,
                        "BlobContainer": blob_container,
                        "BlobName": blob_name,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Update MsMessageAttachment blob did not return a row.")
                    return None
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during update MsMessageAttachment blob: %s", error)
            raise map_database_error(error)

    def delete_by_public_id(self, public_id: str) -> Optional[MsMessageAttachment]:
        """
        Delete a MsMessageAttachment by public ID.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="DeleteMsMessageAttachmentByPublicId",
                    params={
                        "PublicId": public_id,
                    },
                )
                row = cursor.fetchone()
                if not row:
                    logger.error("Delete MsMessageAttachment did not return a row.")
                    return None
                return self._from_db(row)
        except Exception as error:
            logger.error("Error during delete MsMessageAttachment by public ID: %s", error)
            raise map_database_error(error)
