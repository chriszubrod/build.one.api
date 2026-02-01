# Python Standard Library Imports
from dataclasses import dataclass, asdict
from typing import Optional
from decimal import Decimal
import base64

# Third-party Imports

# Local Imports


@dataclass
class QboVendorCreditLine:
    """Local cache model for QBO VendorCredit line items."""
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    qbo_vendor_credit_id: Optional[int]
    qbo_line_id: Optional[str]
    line_num: Optional[int]
    description: Optional[str]
    amount: Optional[Decimal]
    detail_type: Optional[str]
    # Item-based detail fields
    item_ref_value: Optional[str]
    item_ref_name: Optional[str]
    class_ref_value: Optional[str]
    class_ref_name: Optional[str]
    unit_price: Optional[Decimal]
    qty: Optional[Decimal]
    billable_status: Optional[str]
    customer_ref_value: Optional[str]
    customer_ref_name: Optional[str]
    # Account-based detail fields
    account_ref_value: Optional[str]
    account_ref_name: Optional[str]

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


@dataclass
class QboVendorCredit:
    """Local cache model for QBO VendorCredit."""
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    realm_id: Optional[str]
    qbo_id: Optional[str]
    sync_token: Optional[str]
    vendor_ref_value: Optional[str]
    vendor_ref_name: Optional[str]
    txn_date: Optional[str]
    doc_number: Optional[str]
    total_amt: Optional[Decimal]
    private_note: Optional[str]
    ap_account_ref_value: Optional[str]
    ap_account_ref_name: Optional[str]
    currency_ref_value: Optional[str]
    currency_ref_name: Optional[str]

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
