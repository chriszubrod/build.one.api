"""EmailMessage + EmailAttachment dataclasses."""
import base64
from dataclasses import dataclass, asdict
from decimal import Decimal
from typing import Optional


@dataclass
class EmailMessage:
    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None
    graph_message_id: Optional[str] = None
    internet_message_id: Optional[str] = None
    conversation_id: Optional[str] = None
    mailbox_address: Optional[str] = None
    from_address: Optional[str] = None
    from_name: Optional[str] = None
    subject: Optional[str] = None
    body_preview: Optional[str] = None
    body_content: Optional[str] = None
    body_content_type: Optional[str] = None
    received_datetime: Optional[str] = None
    processing_status: Optional[str] = None
    last_error: Optional[str] = None
    agent_session_id: Optional[int] = None
    web_link: Optional[str] = None
    has_attachments: bool = False

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EmailAttachment:
    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None
    email_message_id: Optional[int] = None
    graph_attachment_id: Optional[str] = None
    filename: Optional[str] = None
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    is_inline: bool = False
    blob_uri: Optional[str] = None
    extraction_status: Optional[str] = None
    extracted_at: Optional[str] = None
    di_model: Optional[str] = None
    di_result_json: Optional[str] = None
    di_confidence: Optional[Decimal] = None
    di_vendor_name: Optional[str] = None
    di_invoice_number: Optional[str] = None
    di_invoice_date: Optional[str] = None
    di_due_date: Optional[str] = None
    di_subtotal: Optional[Decimal] = None
    di_total_amount: Optional[Decimal] = None
    di_currency: Optional[str] = None
    last_error: Optional[str] = None

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    def to_dict(self) -> dict:
        return asdict(self)
