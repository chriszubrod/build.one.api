# Python Standard Library Imports
import base64
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Optional


# BudgetRevision status the child lock keys off. Line items belonging to
# an approved revision are immutable (covers Rev 0 of an active budget
# too, since activation approves it).
REVISION_STATUS_APPROVED = "approved"


@dataclass
class BudgetLineItem:
    """
    A single schedule-of-values / change-order delta line on a
    BudgetRevision. All business fields are nullable (auto-save grid
    persists partial rows); negative values are legal (CO deltas).

    Amount = pre-markup cost basis; Price = Amount x (1 + Markup) =
    contract value. Both are client-sent — no derived computation
    server-side in v1.
    """

    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    budget_revision_id: Optional[int]
    sub_cost_code_id: Optional[int] = None
    description: Optional[str] = None
    quantity: Optional[Decimal] = None
    rate: Optional[Decimal] = None
    amount: Optional[Decimal] = None
    markup: Optional[Decimal] = None
    price: Optional[Decimal] = None
    # Join-enriched (read sprocs only — Create/Update OUTPUT cannot join).
    revision_status: Optional[str] = None
    revision_type: Optional[str] = None
    budget_id: Optional[int] = None
    project_id: Optional[int] = None

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    def to_dict(self) -> dict:
        d = asdict(self)
        # DECIMAL transport as strings — never float.
        for k in ("quantity", "rate", "amount", "markup", "price"):
            if d.get(k) is not None:
                d[k] = str(d[k])
        return d
