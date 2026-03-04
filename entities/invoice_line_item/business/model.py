# Python Standard Library Imports
from dataclasses import dataclass, asdict
from typing import Optional
from decimal import Decimal
import base64

# Third-party Imports

# Local Imports


@dataclass
class InvoiceLineItem:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    invoice_id: Optional[int]
    source_type: Optional[str]
    bill_line_item_id: Optional[int]
    expense_line_item_id: Optional[int]
    bill_credit_line_item_id: Optional[int]
    description: Optional[str]
    amount: Optional[Decimal]
    markup: Optional[Decimal]
    price: Optional[Decimal]
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
