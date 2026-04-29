# Python Standard Library Imports
from dataclasses import dataclass, asdict
from typing import Optional
import base64

# Third-party Imports

# Local Imports


@dataclass
class DeviceToken:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    deactivated_datetime: Optional[str]
    user_id: Optional[int]
    token: Optional[str]
    app_bundle_id: Optional[str]
    platform: Optional[str]
    is_active: Optional[bool]

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    def to_dict(self) -> dict:
        return asdict(self)
