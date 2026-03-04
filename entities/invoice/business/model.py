# Python Standard Library Imports
from dataclasses import dataclass, asdict
from typing import Optional
from decimal import Decimal
import base64

# Third-party Imports

# Local Imports


@dataclass
class Invoice:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    project_id: Optional[int]
    payment_term_id: Optional[int]
    invoice_date: Optional[str]
    due_date: Optional[str]
    invoice_number: Optional[str]
    total_amount: Optional[Decimal]
    memo: Optional[str]
    is_draft: Optional[bool]

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
        return asdict(self)
