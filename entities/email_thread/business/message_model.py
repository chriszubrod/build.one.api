from __future__ import annotations

import base64
from dataclasses import dataclass, asdict
from decimal import Decimal
from typing import Optional


@dataclass
class EmailThreadMessage:
    id:                         Optional[int]
    public_id:                  Optional[str]
    row_version:                Optional[str]
    created_datetime:           Optional[str]
    updated_datetime:           Optional[str]
    email_thread_id:            Optional[int]
    inbox_record_id:            Optional[int]
    sender_role:                Optional[str]
    message_position:           Optional[int]
    is_reply:                   Optional[bool]
    is_forward:                 Optional[bool]
    classification:             Optional[str]
    classification_confidence:  Optional[Decimal]
    received_datetime:          Optional[str]

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version is None:
            return None
        return base64.b64decode(self.row_version)

    @property
    def row_version_hex(self) -> Optional[str]:
        b = self.row_version_bytes
        if b is None:
            return None
        return b.hex()

    def to_dict(self) -> dict:
        return asdict(self)
