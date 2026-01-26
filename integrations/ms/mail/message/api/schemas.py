# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, EmailStr, Field


# =============================================================================
# Request Schemas
# =============================================================================

class EmailRecipient(BaseModel):
    """Email recipient with email and optional name."""
    email: EmailStr
    name: Optional[str] = None


class SendMessageRequest(BaseModel):
    """Request to send a new email message."""
    to_recipients: list[EmailRecipient]
    subject: str
    body: str
    body_type: str = Field(default="HTML", pattern="^(HTML|Text)$")
    cc_recipients: Optional[list[EmailRecipient]] = None
    bcc_recipients: Optional[list[EmailRecipient]] = None
    importance: str = Field(default="normal", pattern="^(low|normal|high)$")
    save_to_sent_items: bool = True


class CreateDraftRequest(BaseModel):
    """Request to create a draft message."""
    to_recipients: Optional[list[EmailRecipient]] = None
    subject: str = ""
    body: str = ""
    body_type: str = Field(default="HTML", pattern="^(HTML|Text)$")
    cc_recipients: Optional[list[EmailRecipient]] = None
    bcc_recipients: Optional[list[EmailRecipient]] = None
    importance: str = Field(default="normal", pattern="^(low|normal|high)$")


class UpdateDraftRequest(BaseModel):
    """Request to update an existing draft."""
    to_recipients: Optional[list[EmailRecipient]] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    body_type: str = Field(default="HTML", pattern="^(HTML|Text)$")
    cc_recipients: Optional[list[EmailRecipient]] = None
    bcc_recipients: Optional[list[EmailRecipient]] = None
    importance: Optional[str] = Field(default=None, pattern="^(low|normal|high)$")


class ReplyRequest(BaseModel):
    """Request to reply to a message."""
    body: str
    body_type: str = Field(default="HTML", pattern="^(HTML|Text)$")
    reply_all: bool = False


class ForwardRequest(BaseModel):
    """Request to forward a message."""
    to_recipients: list[EmailRecipient]
    comment: Optional[str] = None


class MoveMessageRequest(BaseModel):
    """Request to move a message to a folder."""
    destination_folder_id: str


class LinkAttachmentRequest(BaseModel):
    """Request to link an attachment."""
    attachment_id: str
    upload_to_blob: bool = True


# =============================================================================
# Response Schemas
# =============================================================================

class MailFolderResponse(BaseModel):
    """Mail folder information."""
    folder_id: str
    display_name: str
    parent_folder_id: Optional[str] = None
    child_folder_count: int = 0
    unread_item_count: int = 0
    total_item_count: int = 0


class EmailRecipientResponse(BaseModel):
    """Email recipient in response."""
    name: Optional[str] = None
    email: Optional[str] = None


class MessageResponse(BaseModel):
    """Email message information."""
    message_id: str
    conversation_id: Optional[str] = None
    internet_message_id: Optional[str] = None
    subject: Optional[str] = None
    from_name: Optional[str] = None
    from_email: Optional[str] = None
    to_recipients: list[EmailRecipientResponse] = []
    cc_recipients: list[EmailRecipientResponse] = []
    received_datetime: Optional[str] = None
    sent_datetime: Optional[str] = None
    is_read: bool = False
    is_draft: bool = False
    has_attachments: bool = False
    importance: str = "normal"
    body_preview: Optional[str] = None
    web_link: Optional[str] = None
    body_content: Optional[str] = None
    body_content_type: Optional[str] = None


class AttachmentResponse(BaseModel):
    """Attachment information."""
    attachment_id: str
    name: str
    content_type: str
    size: Optional[int] = None
    is_inline: bool = False
    attachment_type: Optional[str] = None


class LinkedMessageResponse(BaseModel):
    """Linked message stored locally."""
    id: int
    public_id: str
    message_id: str
    conversation_id: Optional[str] = None
    subject: Optional[str] = None
    from_email: str
    from_name: Optional[str] = None
    received_datetime: Optional[str] = None
    is_read: bool = False
    has_attachments: bool = False
    importance: str = "normal"
    created_datetime: Optional[str] = None


class LinkedAttachmentResponse(BaseModel):
    """Linked attachment stored locally."""
    id: int
    public_id: str
    attachment_id: str
    name: str
    content_type: str
    size: Optional[int] = None
    blob_url: Optional[str] = None
    created_datetime: Optional[str] = None


class ListMessagesResponse(BaseModel):
    """Response for listing messages."""
    message: str
    status_code: int
    messages: list[MessageResponse] = []
    total_count: int = 0
    has_more: bool = False


class ListFoldersResponse(BaseModel):
    """Response for listing mail folders."""
    message: str
    status_code: int
    folders: list[MailFolderResponse] = []


class ListAttachmentsResponse(BaseModel):
    """Response for listing attachments."""
    message: str
    status_code: int
    attachments: list[AttachmentResponse] = []
