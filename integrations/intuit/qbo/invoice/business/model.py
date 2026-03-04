# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional, List
from decimal import Decimal

# Third-party Imports
import base64

# Local Imports


@dataclass
class QboInvoiceLine:
    """
    Represents a line item from a QBO Invoice stored locally.
    """
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    qbo_invoice_id: Optional[int]
    qbo_line_id: Optional[str]
    line_num: Optional[int]
    description: Optional[str]
    amount: Optional[Decimal]
    detail_type: Optional[str]
    item_ref_value: Optional[str]
    item_ref_name: Optional[str]
    class_ref_value: Optional[str]
    class_ref_name: Optional[str]
    qty: Optional[Decimal]
    unit_price: Optional[Decimal]
    tax_code_ref_value: Optional[str]
    tax_code_ref_name: Optional[str]
    service_date: Optional[str]
    discount_rate: Optional[Decimal]
    discount_amt: Optional[Decimal]

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
        Convert the QboInvoiceLine dataclass to a dictionary.
        """
        data = asdict(self)
        for key in ['amount', 'qty', 'unit_price', 'discount_rate', 'discount_amt']:
            if data.get(key) is not None:
                data[key] = float(data[key])
        return data


@dataclass
class QboInvoice:
    """
    Represents a QBO Invoice stored locally.
    """
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    qbo_id: Optional[str]
    sync_token: Optional[str]
    realm_id: Optional[str]
    customer_ref_value: Optional[str]
    customer_ref_name: Optional[str]
    txn_date: Optional[str]
    due_date: Optional[str]
    ship_date: Optional[str]
    doc_number: Optional[str]
    private_note: Optional[str]
    customer_memo: Optional[str]
    bill_email: Optional[str]
    total_amt: Optional[Decimal]
    balance: Optional[Decimal]
    deposit: Optional[Decimal]
    sales_term_ref_value: Optional[str]
    sales_term_ref_name: Optional[str]
    currency_ref_value: Optional[str]
    currency_ref_name: Optional[str]
    exchange_rate: Optional[Decimal]
    department_ref_value: Optional[str]
    department_ref_name: Optional[str]
    class_ref_value: Optional[str]
    class_ref_name: Optional[str]
    ship_method_ref_value: Optional[str]
    ship_method_ref_name: Optional[str]
    tracking_num: Optional[str]
    print_status: Optional[str]
    email_status: Optional[str]
    allow_online_ach_payment: Optional[bool]
    allow_online_credit_card_payment: Optional[bool]
    apply_tax_after_discount: Optional[bool]
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
        Convert the QboInvoice dataclass to a dictionary.
        """
        data = asdict(self)
        for key in ['total_amt', 'balance', 'deposit', 'exchange_rate']:
            if data.get(key) is not None:
                data[key] = float(data[key])
        return data
