# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional
from decimal import Decimal

# Third-party Imports
import base64

# Local Imports


@dataclass
class QboItem:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    qbo_id: Optional[str]
    sync_token: Optional[str]
    realm_id: Optional[str]
    name: Optional[str]
    description: Optional[str]
    active: Optional[bool]
    type: Optional[str]
    parent_ref_value: Optional[str]
    parent_ref_name: Optional[str]
    level: Optional[int]
    fully_qualified_name: Optional[str]
    sku: Optional[str]
    unit_price: Optional[Decimal]
    purchase_cost: Optional[Decimal]
    taxable: Optional[bool]
    income_account_ref_value: Optional[str]
    income_account_ref_name: Optional[str]
    expense_account_ref_value: Optional[str]
    expense_account_ref_name: Optional[str]

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
    def is_parent(self) -> bool:
        """
        Check if this item is a parent (has no ParentRef).
        """
        return self.parent_ref_value is None

    @property
    def is_child(self) -> bool:
        """
        Check if this item is a child (has ParentRef).
        """
        return self.parent_ref_value is not None

    def parse_name(self) -> tuple[str, str]:
        """
        Parse item name to extract number and name.
        Splits by first space to get number (before) and name (after).
        
        Examples:
            "01 Permits" -> ("01", "Permits")
            "01.0 Permits" -> ("01.0", "Permits")
            "02 Site Work" -> ("02", "Site Work")
        
        Returns:
            tuple: (number, name)
            If no space, returns (name, name)
        """
        if not self.name:
            return ("", "")
        
        if " " in self.name:
            parts = self.name.split(" ", 1)
            number = parts[0].strip()
            name = parts[1].strip() if len(parts) > 1 else parts[0].strip()
            return (number, name)
        
        # No space - use full name for both
        return (self.name.strip(), self.name.strip())

    def to_dict(self) -> dict:
        """
        Convert the QboItem dataclass to a dictionary.
        """
        data = asdict(self)
        # Convert Decimal to float for JSON serialization
        if data.get('unit_price') is not None:
            data['unit_price'] = float(data['unit_price'])
        if data.get('purchase_cost') is not None:
            data['purchase_cost'] = float(data['purchase_cost'])
        return data

