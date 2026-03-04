# Python Standard Library Imports
from dataclasses import dataclass, asdict
from typing import Optional
import base64


@dataclass
class InboxRecord:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]

    # MS Graph message ID
    message_id: Optional[str]

    # Workflow status
    status: Optional[str]

    # Submit-for-review metadata
    submitted_to_email: Optional[str]
    submitted_at: Optional[str]

    # Process metadata
    processed_at: Optional[str]
    record_type: Optional[str]
    record_public_id: Optional[str]

    # Classification data (ML training)
    classification_type: Optional[str]
    classification_confidence: Optional[float]
    classification_signals: Optional[str]
    classified_at: Optional[str]
    user_override_type: Optional[str]

    # Email feature columns
    subject: Optional[str]
    from_email: Optional[str]
    from_name: Optional[str]
    has_attachments: Optional[bool]

    # Processing channel
    processed_via: Optional[str]

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
