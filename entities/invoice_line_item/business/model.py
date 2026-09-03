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
    qbo_id: Optional[str] = None
    realm_id: Optional[str] = None

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
class LinkedTxnSibling:
    """
    One row from the sibling set sharing (InvoiceId, LinkedTxnType,
    LinkedTxnId) — see InvoiceLineItemRepository.read_by_linked_txn (U-362c).
    All source-linked invoice lines drawn from ONE multi-line Bill/Expense
    share this key (LinkedTxnId is the source TRANSACTION id, no per-line
    TxnLineId), so the read is a SET, not a single row.

    Wraps the InvoiceLineItem alongside its own InvoiceLineItemSourceProvenance
    content-fingerprint fields (qbo_amount/qbo_description/service_date/
    line_num) — the immutable QBO-pull snapshot, U-272 — because the sibling's
    OWN InvoiceLineItem.amount/description are user-editable and must never
    feed a recognition fingerprint (see dbo.invoice_line_item.sql's
    InvoiceLineItemSourceProvenance table comment).

    `id`/`qbo_id` proxy the wrapped line item so this can drop straight into
    `find_stale_identity_orphan` (duck-typed on those two attributes) without
    a separate unwrap step for the theft-guard membership check.
    """
    line_item: InvoiceLineItem
    line_num: Optional[int] = None
    qbo_amount: Optional[Decimal] = None
    qbo_description: Optional[str] = None
    service_date: Optional[str] = None

    @property
    def id(self) -> Optional[int]:
        return self.line_item.id

    @property
    def qbo_id(self) -> Optional[str]:
        return self.line_item.qbo_id
