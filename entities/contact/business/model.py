# Python Standard Library Imports
from dataclasses import dataclass, asdict
from typing import Optional
import base64

# Third-party Imports

# Local Imports


@dataclass
class Contact:
    id: Optional[str]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    email: Optional[str]
    office_phone: Optional[str]
    mobile_phone: Optional[str]
    fax: Optional[str]
    notes: Optional[str]
    user_id: Optional[int]
    company_id: Optional[int]
    customer_id: Optional[int]
    project_id: Optional[int]
    vendor_id: Optional[int]

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
        Convert the contact dataclass to a dictionary.
        """
        return asdict(self)
