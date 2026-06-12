# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional
import base64


# Revision types — 'original' is Rev 0 (created internally by
# BudgetService.create); 'change_order' is every subsequent delta revision.
VALID_TYPES = ("original", "change_order")

# Status workflow — draft → approved. Approved revisions are immutable and
# are the only revisions that count toward contract value.
VALID_STATUSES = ("draft", "approved")


@dataclass
class BudgetRevision:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    budget_id: Optional[int]
    revision_number: Optional[int] = None
    type: Optional[str] = None
    status: Optional[str] = "draft"
    title: Optional[str] = None
    description: Optional[str] = None
    approved_by_user_id: Optional[int] = None
    approved_datetime: Optional[str] = None
    effective_date: Optional[str] = None
    # Join-enriched (Budget header) — read sprocs join up to Budget so the
    # service can assert project access without an extra round trip.
    budget_public_id: Optional[str] = None
    project_id: Optional[int] = None
    budget_status: Optional[str] = None

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    def to_dict(self) -> dict:
        return asdict(self)
