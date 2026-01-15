# Python Standard Library Imports
from dataclasses import dataclass, asdict
from typing import Optional
import base64

# Third-party Imports

# Local Imports


@dataclass
class QboAttachable:
    """
    Represents a QBO Attachable stored locally.
    """
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    qbo_id: Optional[str]
    sync_token: Optional[str]
    realm_id: Optional[str]
    file_name: Optional[str]
    note: Optional[str]
    category: Optional[str]
    content_type: Optional[str]
    size: Optional[int]
    file_access_uri: Optional[str]
    temp_download_uri: Optional[str]
    # Reference to linked entity
    entity_ref_type: Optional[str]  # e.g., "Bill", "Invoice"
    entity_ref_value: Optional[str]  # QBO ID of the linked entity

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
        """Convert to dictionary with JSON-serializable values."""
        return asdict(self)
