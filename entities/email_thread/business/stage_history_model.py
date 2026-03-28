from __future__ import annotations

import base64
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class EmailThreadStageHistory:
    id:                     Optional[int]
    public_id:              Optional[str]
    row_version:            Optional[str]
    created_datetime:       Optional[str]
    email_thread_id:        Optional[int]
    from_stage:             Optional[str]
    to_stage:               Optional[str]
    triggered_by:           Optional[str]
    user_id:                Optional[int]
    email_thread_message_id: Optional[int]
    notes:                  Optional[str]
    transition_datetime:    Optional[str]

    # No updated_datetime — this table is append-only by design.
    # No UPDATE stored procedures exist for EmailThreadStageHistory.

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
