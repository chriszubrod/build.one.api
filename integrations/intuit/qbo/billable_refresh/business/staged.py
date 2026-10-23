# Python Standard Library Imports
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class StagedBillableQboLine:
    """One qbo.*Line row still staged as Billable, with header QboId for live fetch."""

    id: int
    qbo_line_id: Optional[str]
    amount: Optional[Decimal]
    billable_status: Optional[str]
    header_qbo_id: Optional[str]
    realm_id: Optional[str]
