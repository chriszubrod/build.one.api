# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional
import base64


@dataclass
class MsMessageVendor:
    """
    Model representing a link between MsMessage and Vendor.
    """
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    ms_message_id: Optional[int]
    vendor_id: Optional[int]
    notes: Optional[str]

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return asdict(self)
