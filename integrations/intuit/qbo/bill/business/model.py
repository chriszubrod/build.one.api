# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional, List
from decimal import Decimal

# Third-party Imports
import base64

# Local Imports


@dataclass
class QboBillLine:
    """
    Represents a line item from a QBO Bill stored locally.
    """
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    qbo_bill_id: Optional[int]
    qbo_line_id: Optional[str]
    line_num: Optional[int]
    description: Optional[str]
    amount: Optional[Decimal]
    detail_type: Optional[str]
    item_ref_value: Optional[str]
    item_ref_name: Optional[str]
    account_ref_value: Optional[str]
    account_ref_name: Optional[str]
    customer_ref_value: Optional[str]
    customer_ref_name: Optional[str]
    class_ref_value: Optional[str]
    class_ref_name: Optional[str]
    billable_status: Optional[str]
    qty: Optional[Decimal]
    unit_price: Optional[Decimal]
    markup_percent: Optional[Decimal]

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
        Convert the QboBillLine dataclass to a dictionary.
        """
        data = asdict(self)
        # Convert Decimal to float for JSON serialization
        for key in ['amount', 'qty', 'unit_price', 'markup_percent']:
            if data.get(key) is not None:
                data[key] = float(data[key])
        return data


@dataclass
class QboBill:
    """
    Represents a QBO Bill stored locally.
    """
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    qbo_id: Optional[str]
    sync_token: Optional[str]
    realm_id: Optional[str]
    vendor_ref_value: Optional[str]
    vendor_ref_name: Optional[str]
    txn_date: Optional[str]
    due_date: Optional[str]
    doc_number: Optional[str]
    private_note: Optional[str]
    total_amt: Optional[Decimal]
    balance: Optional[Decimal]
    ap_account_ref_value: Optional[str]
    ap_account_ref_name: Optional[str]
    sales_term_ref_value: Optional[str]
    sales_term_ref_name: Optional[str]
    currency_ref_value: Optional[str]
    currency_ref_name: Optional[str]
    exchange_rate: Optional[Decimal]
    department_ref_value: Optional[str]
    department_ref_name: Optional[str]
    global_tax_calculation: Optional[str]

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
        Convert the QboBill dataclass to a dictionary.
        """
        data = asdict(self)
        # Convert Decimal to float for JSON serialization
        for key in ['total_amt', 'balance', 'exchange_rate']:
            if data.get(key) is not None:
                data[key] = float(data[key])
        return data
