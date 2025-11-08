# Python Standard Library Imports
import base64
from dataclasses import asdict, dataclass
from typing import Optional

# Third-party Imports

# Local Imports


@dataclass
class MapVendorQboVendor:
    id: Optional[str]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    vendor_id: Optional[str]
    qbo_vendor_id: Optional[str]
    
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
        Convert the map vendor qbo vendor dataclass to a dictionary.
        """
        return asdict(self)
