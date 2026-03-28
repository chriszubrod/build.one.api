# Python Standard Library Imports
from dataclasses import dataclass, asdict
from typing import Optional
import base64


@dataclass
class InboxRecord:
    id: Optional[int]                           = None
    public_id: Optional[str]                    = None
    row_version: Optional[str]                  = None
    created_datetime: Optional[str]             = None
    modified_datetime: Optional[str]            = None

    # MS Graph message ID
    message_id: Optional[str]                   = None

    # Workflow status
    status: Optional[str]                       = None

    # Submit-for-review metadata
    submitted_to_email: Optional[str]           = None
    submitted_at: Optional[str]                 = None

    # Process metadata
    processed_at: Optional[str]                 = None
    record_type: Optional[str]                  = None
    record_public_id: Optional[str]             = None

    # Classification data (ML training)
    classification_type: Optional[str]          = None
    classification_confidence: Optional[float]  = None
    classification_signals: Optional[str]       = None
    classified_at: Optional[str]                = None
    user_override_type: Optional[str]           = None

    # Email feature columns
    subject: Optional[str]                      = None
    from_email: Optional[str]                   = None
    from_name: Optional[str]                    = None
    has_attachments: Optional[bool]             = None

    # Processing channel
    processed_via: Optional[str]                = None

    # Email threading headers
    internet_message_id: Optional[str]          = None
    conversation_id: Optional[str]              = None

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
        return asdict(self)
