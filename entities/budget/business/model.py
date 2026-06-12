# Python Standard Library Imports
from dataclasses import asdict, dataclass
from typing import Optional
import base64


# Status workflow — draft → active (via ActivateBudgetById only) → archived
# (reserved for future re-baselining). Status NEVER changes via update.
VALID_STATUSES = ("draft", "active", "archived")


@dataclass
class Budget:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]
    project_id: Optional[int] = None
    status: Optional[str] = "draft"
    notes: Optional[str] = None
    # Join-enriched (ReadBudgets / by-id reads LEFT JOIN Project).
    project_name: Optional[str] = None
    project_public_id: Optional[str] = None

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    def to_dict(self) -> dict:
        return asdict(self)
