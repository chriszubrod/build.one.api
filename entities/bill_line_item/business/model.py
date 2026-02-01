# Python Standard Library Imports
from dataclasses import dataclass, asdict
from typing import Optional
from decimal import Decimal
import base64

# Third-party Imports

# Local Imports


@dataclass
class BillLineItem:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    bill_id: Optional[int]
    sub_cost_code_id: Optional[int]
    project_id: Optional[int]
    description: Optional[str]
    quantity: Optional[int]
    rate: Optional[Decimal]
    amount: Optional[Decimal]
    is_billable: Optional[bool]
    is_billed: Optional[bool]
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
        """
        Convert the bill line item dataclass to a dictionary.
        """
        return asdict(self)
