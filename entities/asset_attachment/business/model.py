# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional
import base64


@dataclass
class AssetAttachment:
    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None
    asset_id: Optional[int] = None
    attachment_id: Optional[int] = None

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    def to_dict(self) -> dict:
        return asdict(self)
