# Python Standard Library Imports
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Optional
import base64

# Third-party Imports

# Local Imports


@dataclass
class ExpenseCodingItem:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    qbo_purchase_id: Optional[int]
    qbo_purchase_line_id: Optional[int]
    qbo_line_id: Optional[str]
    qbo_purchase_qbo_id: Optional[str]
    realm_id: Optional[str]
    vendor_id: Optional[int]
    sync_token_at_suggest: Optional[str]
    status: Optional[str]
    suggested_project_id: Optional[int]
    suggested_sub_cost_code_id: Optional[int]
    suggested_description: Optional[str]
    suggestion_source: Optional[str]
    suggestion_reason: Optional[str]
    suggestion_confidence: Optional[Decimal]
    suggested_at: Optional[str]
    confirmed_project_id: Optional[int]
    confirmed_sub_cost_code_id: Optional[int]
    confirmed_description: Optional[str]
    was_overridden: Optional[bool]
    confirmed_by_user_id: Optional[int]
    confirmed_at: Optional[str]
    flag_reason: Optional[str]
    flagged_at: Optional[str]
    written_at: Optional[str]
    write_error: Optional[str]
    claimed_by_user_id: Optional[int]
    claimed_at: Optional[str]
    company_id: Optional[int]
    created_by_user_id: Optional[int]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    def to_dict(self) -> dict:
        data = asdict(self)
        if data.get("suggestion_confidence") is not None:
            data["suggestion_confidence"] = str(data["suggestion_confidence"])
        return data
