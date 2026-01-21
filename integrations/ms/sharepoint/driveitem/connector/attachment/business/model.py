# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional

# Third-party Imports
import base64

# Local Imports


@dataclass
class DriveItemAttachment:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    attachment_id: Optional[int]
    ms_driveitem_id: Optional[int]

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
        """
        Convert the DriveItemAttachment dataclass to a dictionary.
        """
        return asdict(self)
