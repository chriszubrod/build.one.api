# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional
import base64


@dataclass
class MsMessage:
    """
    Model representing a linked email message stored locally.
    """
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    
    # Graph API identifiers
    message_id: Optional[str]
    conversation_id: Optional[str]
    internet_message_id: Optional[str]
    
    # Message metadata
    subject: Optional[str]
    from_email: Optional[str]
    from_name: Optional[str]
    received_datetime: Optional[str]
    sent_datetime: Optional[str]
    
    # Body content
    body_content_type: Optional[str]
    body: Optional[str]
    body_preview: Optional[str]
    
    # Flags
    is_read: Optional[bool]
    has_attachments: Optional[bool]
    importance: Optional[str]
    
    # Web link
    web_link: Optional[str]

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    @property
    def row_version_hex(self) -> Optional[str]:
        if self.row_version_bytes:
            return self.row_version_bytes.hex()
        return None

    def to_dict(self) -> dict:
        """Convert the MsMessage dataclass to a dictionary."""
        return asdict(self)


@dataclass
class MsMessageRecipient:
    """
    Model representing a recipient of a linked message.
    """
    id: Optional[int]
    ms_message_id: Optional[int]
    recipient_type: Optional[str]  # TO, CC, BCC
    email: Optional[str]
    name: Optional[str]

    def to_dict(self) -> dict:
        """Convert the MsMessageRecipient dataclass to a dictionary."""
        return asdict(self)


@dataclass
class MsMessageAttachment:
    """
    Model representing an attachment of a linked message.
    """
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    
    ms_message_id: Optional[int]
    attachment_id: Optional[str]  # Graph attachment ID
    
    # Attachment metadata
    name: Optional[str]
    content_type: Optional[str]
    size: Optional[int]
    is_inline: Optional[bool]
    
    # Azure Blob storage reference
    blob_url: Optional[str]
    blob_container: Optional[str]
    blob_name: Optional[str]

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    @property
    def row_version_hex(self) -> Optional[str]:
        if self.row_version_bytes:
            return self.row_version_bytes.hex()
        return None

    def to_dict(self) -> dict:
        """Convert the MsMessageAttachment dataclass to a dictionary."""
        return asdict(self)
