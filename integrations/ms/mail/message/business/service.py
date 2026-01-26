# Python Standard Library Imports
from typing import Optional
import base64
import logging

# Third-party Imports

# Local Imports
from integrations.ms.mail.message.business.model import (
    MsMessage,
    MsMessageRecipient,
    MsMessageAttachment,
)
from integrations.ms.mail.message.persistence.repo import (
    MsMessageRepository,
    MsMessageRecipientRepository,
    MsMessageAttachmentRepository,
)
from integrations.ms.mail.external.client import (
    list_mail_folders as graph_list_mail_folders,
    get_mail_folder as graph_get_mail_folder,
    list_messages as graph_list_messages,
    get_message as graph_get_message,
    mark_message_read as graph_mark_message_read,
    list_message_attachments as graph_list_message_attachments,
    download_attachment as graph_download_attachment,
    send_message as graph_send_message,
    create_draft as graph_create_draft,
    update_draft as graph_update_draft,
    send_draft as graph_send_draft,
    reply_to_message as graph_reply_to_message,
    create_reply_draft as graph_create_reply_draft,
    forward_message as graph_forward_message,
    create_forward_draft as graph_create_forward_draft,
    move_message as graph_move_message,
    delete_message as graph_delete_message,
)

logger = logging.getLogger(__name__)


def _upload_to_blob(container_name: str, blob_name: str, content: bytes, content_type: str) -> Optional[str]:
    """
    Upload content to Azure Blob Storage.
    Returns blob URL on success, None on failure or if storage not configured.
    """
    try:
        from shared.storage import AzureBlobStorage
        storage = AzureBlobStorage()
        blob_url = storage.upload_file(
            blob_name=blob_name,
            file_content=content,
            content_type=content_type,
            container_name=container_name
        )
        return blob_url
    except ValueError as e:
        # Storage not configured
        logger.warning(f"Azure Blob Storage not configured: {e}")
        return None
    except Exception as e:
        logger.error(f"Error uploading to blob storage: {e}")
        return None


class MsMessageService:
    """
    Service for MS Mail message operations.
    Combines Graph API calls with local persistence for linked messages.
    """

    def __init__(
        self,
        repo: Optional[MsMessageRepository] = None,
        recipient_repo: Optional[MsMessageRecipientRepository] = None,
        attachment_repo: Optional[MsMessageAttachmentRepository] = None
    ):
        """Initialize the MsMessageService."""
        self.repo = repo or MsMessageRepository()
        self.recipient_repo = recipient_repo or MsMessageRecipientRepository()
        self.attachment_repo = attachment_repo or MsMessageAttachmentRepository()

    # =========================================================================
    # Pass-through Graph API operations (no local storage)
    # =========================================================================

    def list_folders(self) -> dict:
        """
        List all mail folders from Graph API.
        Pass-through, no local storage.
        """
        return graph_list_mail_folders()

    def get_folder(self, folder_id: str) -> dict:
        """
        Get a specific mail folder from Graph API.
        Pass-through, no local storage.
        """
        return graph_get_mail_folder(folder_id)

    def list_messages(
        self,
        folder: str = "inbox",
        top: int = 25,
        skip: int = 0,
        filter_query: Optional[str] = None,
        search: Optional[str] = None,
        order_by: str = "receivedDateTime desc"
    ) -> dict:
        """
        List messages from Graph API.
        Pass-through, no local storage.
        """
        return graph_list_messages(
            folder=folder,
            top=top,
            skip=skip,
            filter_query=filter_query,
            search=search,
            order_by=order_by
        )

    def get_message(self, message_id: str, include_body: bool = True) -> dict:
        """
        Get a specific message from Graph API.
        Pass-through, no local storage.
        """
        return graph_get_message(message_id, include_body=include_body)

    def mark_message_read(self, message_id: str, is_read: bool = True) -> dict:
        """
        Mark a message as read/unread in Graph API.
        """
        return graph_mark_message_read(message_id, is_read=is_read)

    def list_attachments(self, message_id: str) -> dict:
        """
        List attachments for a message from Graph API.
        """
        return graph_list_message_attachments(message_id)

    def download_attachment(self, message_id: str, attachment_id: str) -> dict:
        """
        Download an attachment from Graph API.
        """
        return graph_download_attachment(message_id, attachment_id)

    # =========================================================================
    # Send & Draft operations
    # =========================================================================

    def send_message(
        self,
        to_recipients: list[dict],
        subject: str,
        body: str,
        body_type: str = "HTML",
        cc_recipients: Optional[list[dict]] = None,
        bcc_recipients: Optional[list[dict]] = None,
        attachments: Optional[list[dict]] = None,
        importance: str = "normal",
        save_to_sent_items: bool = True
    ) -> dict:
        """
        Send a new email message via Graph API.
        """
        return graph_send_message(
            to_recipients=to_recipients,
            subject=subject,
            body=body,
            body_type=body_type,
            cc_recipients=cc_recipients,
            bcc_recipients=bcc_recipients,
            attachments=attachments,
            importance=importance,
            save_to_sent_items=save_to_sent_items
        )

    def create_draft(
        self,
        to_recipients: Optional[list[dict]] = None,
        subject: str = "",
        body: str = "",
        body_type: str = "HTML",
        cc_recipients: Optional[list[dict]] = None,
        bcc_recipients: Optional[list[dict]] = None,
        attachments: Optional[list[dict]] = None,
        importance: str = "normal"
    ) -> dict:
        """
        Create a draft message in Graph API.
        """
        return graph_create_draft(
            to_recipients=to_recipients,
            subject=subject,
            body=body,
            body_type=body_type,
            cc_recipients=cc_recipients,
            bcc_recipients=bcc_recipients,
            attachments=attachments,
            importance=importance
        )

    def update_draft(
        self,
        message_id: str,
        to_recipients: Optional[list[dict]] = None,
        subject: Optional[str] = None,
        body: Optional[str] = None,
        body_type: str = "HTML",
        cc_recipients: Optional[list[dict]] = None,
        bcc_recipients: Optional[list[dict]] = None,
        importance: Optional[str] = None
    ) -> dict:
        """
        Update an existing draft message.
        """
        return graph_update_draft(
            message_id=message_id,
            to_recipients=to_recipients,
            subject=subject,
            body=body,
            body_type=body_type,
            cc_recipients=cc_recipients,
            bcc_recipients=bcc_recipients,
            importance=importance
        )

    def send_draft(self, message_id: str) -> dict:
        """
        Send an existing draft message.
        """
        return graph_send_draft(message_id)

    def reply_to_message(
        self,
        message_id: str,
        body: str,
        body_type: str = "HTML",
        reply_all: bool = False
    ) -> dict:
        """
        Reply to a message.
        """
        return graph_reply_to_message(
            message_id=message_id,
            body=body,
            body_type=body_type,
            reply_all=reply_all
        )

    def create_reply_draft(self, message_id: str, reply_all: bool = False) -> dict:
        """
        Create a reply draft for more control before sending.
        """
        return graph_create_reply_draft(message_id, reply_all=reply_all)

    def forward_message(
        self,
        message_id: str,
        to_recipients: list[dict],
        comment: Optional[str] = None
    ) -> dict:
        """
        Forward a message to recipients.
        """
        return graph_forward_message(
            message_id=message_id,
            to_recipients=to_recipients,
            comment=comment
        )

    def create_forward_draft(self, message_id: str) -> dict:
        """
        Create a forward draft for more control before sending.
        """
        return graph_create_forward_draft(message_id)

    def move_message(self, message_id: str, destination_folder_id: str) -> dict:
        """
        Move a message to a different folder.
        """
        return graph_move_message(message_id, destination_folder_id)

    def delete_message_from_graph(self, message_id: str) -> dict:
        """
        Delete a message from Graph API.
        """
        return graph_delete_message(message_id)

    # =========================================================================
    # Link & Store operations (on-demand persistence)
    # =========================================================================

    def link_message(self, graph_message_id: str) -> dict:
        """
        Link a message by fetching from Graph API and storing locally.
        Used when user explicitly wants to save a message reference.
        
        Args:
            graph_message_id: The Graph API message ID
        
        Returns:
            Dict with status_code, message, and linked message data
        """
        # Check if already linked
        existing = self.repo.read_by_message_id(graph_message_id)
        if existing:
            return {
                "message": "Message is already linked",
                "status_code": 200,
                "email": existing.to_dict()
            }
        
        # Fetch from Graph API
        graph_result = graph_get_message(graph_message_id, include_body=True)
        
        if graph_result.get("status_code") != 200:
            return {
                "message": graph_result.get("message", "Failed to fetch message from Graph"),
                "status_code": graph_result.get("status_code", 500),
                "email": None
            }
        
        email_data = graph_result.get("email")
        if not email_data:
            return {
                "message": "No message data returned from Graph",
                "status_code": 500,
                "email": None
            }
        
        try:
            # Store message
            ms_message = self.repo.create(
                message_id=email_data.get("message_id"),
                conversation_id=email_data.get("conversation_id"),
                internet_message_id=email_data.get("internet_message_id"),
                subject=email_data.get("subject"),
                from_email=email_data.get("from_email"),
                from_name=email_data.get("from_name"),
                received_datetime=email_data.get("received_datetime"),
                sent_datetime=email_data.get("sent_datetime"),
                body_content_type=email_data.get("body_content_type", "HTML"),
                body=email_data.get("body_content"),
                body_preview=email_data.get("body_preview"),
                is_read=email_data.get("is_read", False),
                has_attachments=email_data.get("has_attachments", False),
                importance=email_data.get("importance", "normal"),
                web_link=email_data.get("web_link"),
            )
            
            # Store recipients
            for recipient in email_data.get("to_recipients", []):
                self.recipient_repo.create(
                    ms_message_id=ms_message.id,
                    recipient_type="TO",
                    email=recipient.get("email"),
                    name=recipient.get("name"),
                )
            
            for recipient in email_data.get("cc_recipients", []):
                self.recipient_repo.create(
                    ms_message_id=ms_message.id,
                    recipient_type="CC",
                    email=recipient.get("email"),
                    name=recipient.get("name"),
                )
            
            for recipient in email_data.get("bcc_recipients", []):
                self.recipient_repo.create(
                    ms_message_id=ms_message.id,
                    recipient_type="BCC",
                    email=recipient.get("email"),
                    name=recipient.get("name"),
                )
            
            return {
                "message": "Message linked successfully",
                "status_code": 201,
                "email": ms_message.to_dict()
            }
        except Exception as e:
            logger.exception("Error linking message")
            return {
                "message": f"Error linking message: {str(e)}",
                "status_code": 500,
                "email": None
            }

    def link_message_attachment(
        self,
        message_public_id: str,
        graph_attachment_id: str,
        upload_to_blob: bool = True
    ) -> dict:
        """
        Link an attachment by downloading from Graph and optionally storing in Azure Blob.
        
        Args:
            message_public_id: The local message public ID
            graph_attachment_id: The Graph API attachment ID
            upload_to_blob: Whether to upload content to Azure Blob Storage
        
        Returns:
            Dict with status_code, message, and attachment data
        """
        # Get the linked message
        message = self.repo.read_by_public_id(message_public_id)
        if not message:
            return {
                "message": "Linked message not found",
                "status_code": 404,
                "attachment": None
            }
        
        # Download from Graph
        download_result = graph_download_attachment(message.message_id, graph_attachment_id)
        
        if download_result.get("status_code") != 200:
            return {
                "message": download_result.get("message", "Failed to download attachment"),
                "status_code": download_result.get("status_code", 500),
                "attachment": None
            }
        
        content = download_result.get("content")
        filename = download_result.get("filename")
        content_type = download_result.get("content_type")
        size = download_result.get("size")
        
        try:
            blob_url = None
            blob_container = None
            blob_name = None
            
            # Upload to Azure Blob if requested
            if upload_to_blob and content:
                blob_container = "email-attachments"
                blob_name = f"{message_public_id}/{filename}"
                
                blob_url = _upload_to_blob(
                    container_name=blob_container,
                    blob_name=blob_name,
                    content=content,
                    content_type=content_type
                )
            
            # Store attachment metadata
            attachment = self.attachment_repo.create(
                ms_message_id=message.id,
                attachment_id=graph_attachment_id,
                name=filename,
                content_type=content_type,
                size=size,
                is_inline=False,
                blob_url=blob_url,
                blob_container=blob_container,
                blob_name=blob_name,
            )
            
            return {
                "message": "Attachment linked successfully",
                "status_code": 201,
                "attachment": attachment.to_dict()
            }
        except Exception as e:
            logger.exception("Error linking attachment")
            return {
                "message": f"Error linking attachment: {str(e)}",
                "status_code": 500,
                "attachment": None
            }

    # =========================================================================
    # Read linked messages from local storage
    # =========================================================================

    def read_all_linked(self) -> list[MsMessage]:
        """
        Read all linked messages from local storage.
        """
        return self.repo.read_all()

    def read_linked_by_public_id(self, public_id: str) -> Optional[MsMessage]:
        """
        Read a linked message by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_linked_by_message_id(self, message_id: str) -> Optional[MsMessage]:
        """
        Read a linked message by Graph message ID.
        """
        return self.repo.read_by_message_id(message_id)

    def read_linked_by_conversation_id(self, conversation_id: str) -> list[MsMessage]:
        """
        Read all linked messages in a conversation thread.
        """
        return self.repo.read_by_conversation_id(conversation_id)

    def read_linked_by_from_email(self, from_email: str) -> list[MsMessage]:
        """
        Read all linked messages from a specific sender.
        """
        return self.repo.read_by_from_email(from_email)

    def read_linked_recipients(self, message_public_id: str) -> dict:
        """
        Read recipients for a linked message.
        """
        message = self.repo.read_by_public_id(message_public_id)
        if not message:
            return {
                "message": "Linked message not found",
                "status_code": 404,
                "recipients": []
            }
        
        recipients = self.recipient_repo.read_by_ms_message_id(message.id)
        
        return {
            "message": f"Found {len(recipients)} recipients",
            "status_code": 200,
            "recipients": [r.to_dict() for r in recipients]
        }

    def read_linked_attachments(self, message_public_id: str) -> dict:
        """
        Read attachments for a linked message.
        """
        message = self.repo.read_by_public_id(message_public_id)
        if not message:
            return {
                "message": "Linked message not found",
                "status_code": 404,
                "attachments": []
            }
        
        attachments = self.attachment_repo.read_by_ms_message_id(message.id)
        
        return {
            "message": f"Found {len(attachments)} attachments",
            "status_code": 200,
            "attachments": [a.to_dict() for a in attachments]
        }

    def unlink_message(self, public_id: str) -> dict:
        """
        Unlink a message by removing it from local storage.
        Does not delete from Graph API.
        
        Args:
            public_id: The public ID of the linked message
        
        Returns:
            Dict with status_code, message, and deleted message data
        """
        existing = self.repo.read_by_public_id(public_id)
        if not existing:
            return {
                "message": "Linked message not found",
                "status_code": 404,
                "email": None
            }
        
        try:
            deleted = self.repo.delete_by_public_id(public_id)
            
            if deleted:
                return {
                    "message": "Message unlinked successfully",
                    "status_code": 200,
                    "email": deleted.to_dict()
                }
            else:
                return {
                    "message": "Failed to unlink message",
                    "status_code": 500,
                    "email": None
                }
        except Exception as e:
            logger.exception("Error unlinking message")
            return {
                "message": f"Error unlinking message: {str(e)}",
                "status_code": 500,
                "email": None
            }

    # =========================================================================
    # Full message view with recipients and attachments
    # =========================================================================

    def get_linked_message_full(self, public_id: str) -> dict:
        """
        Get a linked message with all recipients and attachments.
        
        Args:
            public_id: The public ID of the linked message
        
        Returns:
            Dict with message, recipients, and attachments
        """
        message = self.repo.read_by_public_id(public_id)
        if not message:
            return {
                "message": "Linked message not found",
                "status_code": 404,
                "email": None,
                "recipients": [],
                "attachments": []
            }
        
        recipients = self.recipient_repo.read_by_ms_message_id(message.id)
        attachments = self.attachment_repo.read_by_ms_message_id(message.id)
        
        # Group recipients by type
        to_recipients = [r.to_dict() for r in recipients if r.recipient_type == "TO"]
        cc_recipients = [r.to_dict() for r in recipients if r.recipient_type == "CC"]
        bcc_recipients = [r.to_dict() for r in recipients if r.recipient_type == "BCC"]
        
        return {
            "message": "Message retrieved successfully",
            "status_code": 200,
            "email": message.to_dict(),
            "to_recipients": to_recipients,
            "cc_recipients": cc_recipients,
            "bcc_recipients": bcc_recipients,
            "attachments": [a.to_dict() for a in attachments]
        }
