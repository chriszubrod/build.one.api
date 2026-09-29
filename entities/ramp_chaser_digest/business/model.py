# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional
import base64


@dataclass
class RampChaserDigest:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    card_holder_ramp_user_id: Optional[str]
    week_of: Optional[str]
    draft_message_id: Optional[str]
    conversation_id: Optional[str]
    internet_message_id: Optional[str]
    last_drafted_at: Optional[str]
    last_notified_at: Optional[str]
    notify_count: Optional[int]
    outcome: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    def to_dict(self) -> dict:
        return asdict(self)
