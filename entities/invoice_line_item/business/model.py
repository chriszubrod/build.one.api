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
    # Phase 3 — EmployeeLabor source for invoice lines that came from
    # internal-employee time aggregation (no Bill in the chain).
    employee_labor_line_item_id: Optional[int] = None
    sub_cost_code_id: Optional[int] = None
    description: Optional[str] = None
    quantity: Optional[Decimal] = None
    rate: Optional[Decimal] = None
    amount: Optional[Decimal] = None
    markup: Optional[Decimal] = None
    price: Optional[Decimal] = None
    is_draft: Optional[bool] = None

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
