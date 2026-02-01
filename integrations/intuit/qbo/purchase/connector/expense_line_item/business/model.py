# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional

# Third-party Imports
import base64

# Local Imports


@dataclass
class PurchaseLineExpenseLineItem:
    """
    Mapping table between QboPurchaseLine integration and ExpenseLineItem module.
    Maintains 1:1 relationship between QboPurchaseLine and ExpenseLineItem.
    """
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    qbo_purchase_line_id: Optional[int]
    expense_line_item_id: Optional[int]

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
        Convert the PurchaseLineExpenseLineItem dataclass to a dictionary.
        """
        return asdict(self)
