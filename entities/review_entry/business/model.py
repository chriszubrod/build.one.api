# Python Standard Library Imports
from dataclasses import asdict, dataclass, field
from typing import Optional
import base64

# Third-party Imports

# Local Imports


@dataclass
class ReviewEntry:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    review_status_id: Optional[int]
    bill_id: Optional[int]
    user_id: Optional[int]
    comments: Optional[str]
    # Denormalized fields from JOINs (populated by read procedures)
    status_name: Optional[str] = field(default=None)
    status_sort_order: Optional[int] = field(default=None)
    status_is_final: Optional[bool] = field(default=None)
    status_is_declined: Optional[bool] = field(default=None)
    status_color: Optional[str] = field(default=None)
    user_firstname: Optional[str] = field(default=None)
    user_lastname: Optional[str] = field(default=None)

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

    @property
    def user_display_name(self) -> Optional[str]:
        parts = [p for p in [self.user_firstname, self.user_lastname] if p]
        return " ".join(parts) if parts else None

    def to_dict(self) -> dict:
        """
        Convert the review entry dataclass to a dictionary.
        """
        d = asdict(self)
        d["user_display_name"] = self.user_display_name
        return d
