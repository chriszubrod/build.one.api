# entities/device_token/business/model.py

from __future__ import annotations

import base64
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class DeviceToken:
    id:                 Optional[int]
    public_id:          Optional[str]
    row_version:        Optional[str]
    created_datetime:   Optional[str]
    updated_datetime:   Optional[str]
    user_id:            Optional[int]
    device_token:       Optional[str]
    device_type:        Optional[str]   # 'ios'
    app_bundle_id:      Optional[str]
    is_active:          Optional[bool]
    last_seen_datetime: Optional[str]

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
