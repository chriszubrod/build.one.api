# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional
import base64

# Third-party Imports

# Local Imports


@dataclass
class PaymentTerm:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    name: Optional[str]
    description: Optional[str]
    discount_percent: Optional[float]
    discount_days: Optional[int]
    due_days: Optional[int]

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
        Convert the payment term dataclass to a dictionary.
        """
        return asdict(self)
