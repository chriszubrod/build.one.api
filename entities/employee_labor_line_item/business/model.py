# Python Standard Library Imports
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Optional
import base64


@dataclass
class EmployeeLaborLineItem:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    employee_labor_id: Optional[int]
    line_date: Optional[str] = None
    project_id: Optional[int] = None
    sub_cost_code_id: Optional[int] = None
    description: Optional[str] = None
    hours: Optional[Decimal] = None
    rate: Optional[Decimal] = None
    markup: Optional[Decimal] = None
    price: Optional[Decimal] = None
    is_billable: Optional[bool] = True
    is_overhead: Optional[bool] = False
    invoice_line_item_id: Optional[int] = None

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("hours", "rate", "markup", "price"):
            if d.get(k) is not None:
                d[k] = str(d[k])
        return d
