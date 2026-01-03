# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional
import base64

# Third-party Imports

# Local Imports


@dataclass
class Attachment:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    filename: Optional[str]
    original_filename: Optional[str]
    file_extension: Optional[str]
    content_type: Optional[str]
    file_size: Optional[int]
    file_hash: Optional[str]
    blob_url: Optional[str]
    description: Optional[str]
    category: Optional[str]
    tags: Optional[str]
    is_archived: Optional[bool]
    status: Optional[str]
    download_count: Optional[int]
    last_downloaded_datetime: Optional[str]
    expiration_date: Optional[str]
    storage_tier: Optional[str]

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
        Convert the attachment dataclass to a dictionary.
        """
        return asdict(self)

