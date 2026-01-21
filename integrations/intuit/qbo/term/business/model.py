# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional
from decimal import Decimal

# Third-party Imports
import base64

# Local Imports


@dataclass
class QboTerm:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    qbo_id: Optional[str]
    sync_token: Optional[str]
    realm_id: Optional[str]
    name: Optional[str]
    discount_percent: Optional[Decimal]
    discount_days: Optional[int]
    active: Optional[bool]
    type: Optional[str]
    day_of_month_due: Optional[int]
    discount_day_of_month: Optional[int]
    due_next_month_days: Optional[int]
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
        Convert the QboTerm dataclass to a dictionary.
        """
        data = asdict(self)
        # Convert Decimal to float for JSON serialization
        if data.get('discount_percent') is not None:
            data['discount_percent'] = float(data['discount_percent'])
        return data
