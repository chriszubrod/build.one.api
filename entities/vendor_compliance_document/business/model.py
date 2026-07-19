# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional
import base64

# Third-party Imports

# Local Imports


@dataclass
class VendorComplianceDocument:
    id: Optional[str]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    vendor_id: Optional[str]
    document_type: Optional[str]
    issuing_authority: Optional[str]
    document_number: Optional[str]
    classification: Optional[str]
    issue_date: Optional[str]
    expiry_date: Optional[str]
    attachment_id: Optional[str]
    verification_status: Optional[str]
    created_by_user_id: Optional[str]

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
        Convert the vendor compliance document dataclass to a dictionary.
        """
        return asdict(self)
