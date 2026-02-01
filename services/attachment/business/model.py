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
    # AI Extraction fields
    extraction_status: Optional[str] = None  # pending, processing, completed, failed
    extracted_text_blob_url: Optional[str] = None  # URL to JSON file in blob storage
    extraction_error: Optional[str] = None
    extracted_datetime: Optional[str] = None
    # AI Categorization fields
    ai_category: Optional[str] = None  # DocumentCategory value
    ai_category_confidence: Optional[float] = None  # 0.0 to 1.0
    ai_category_status: Optional[str] = None  # auto_assigned, suggested, manual, confirmed
    ai_category_reasoning: Optional[str] = None
    ai_extracted_fields: Optional[str] = None  # JSON string
    categorized_datetime: Optional[str] = None

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

