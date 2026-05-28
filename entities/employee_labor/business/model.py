# Python Standard Library Imports
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Optional
import base64


# Status workflow — same shape as ContractLabor but terminal state is
# 'invoiced' (no Bill is generated for employee labor; the InvoiceLineItem
# pointing at this row is the terminal event).
VALID_STATUSES = ("pending_review", "ready", "invoiced")


@dataclass
class EmployeeLabor:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    employee_id: Optional[int]
    project_id: Optional[int] = None
    work_date: Optional[str] = None
    billing_period_start: Optional[str] = None
    billing_period_end: Optional[str] = None
    total_hours: Optional[Decimal] = None
    hourly_rate: Optional[Decimal] = None
    markup: Optional[Decimal] = None
    total_amount: Optional[Decimal] = None
    sub_cost_code_id: Optional[int] = None
    description: Optional[str] = None
    status: Optional[str] = "pending_review"
    source_time_entry_id: Optional[int] = None
    invoice_line_item_id: Optional[int] = None
    # Join-enriched.
    employee_name: Optional[str] = None
    project_name: Optional[str] = None

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("total_hours", "hourly_rate", "markup", "total_amount"):
            if d.get(k) is not None:
                d[k] = str(d[k])
        return d
