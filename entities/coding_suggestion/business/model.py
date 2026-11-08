# Python Standard Library Imports
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Optional
import base64
import json

# Third-party Imports

# Local Imports


# U-551b-f1: ConfirmationSource discriminator for a completion repoint. MUST fit the column and
# the sproc param (NVARCHAR(20)) - a longer value is truncated silently and never matches.
REPOINT_CONFIRMATION_SOURCE = "complete_repoint"

# U-551b-f1-f9: the ONE coding-suggestion status policy. Every confirm guard, submit pre-filter,
# completion candidate filter and drain worker reads these instead of respelling the status strings.
TERMINAL_STATUSES = frozenset({"written", "resolved_externally"})
IN_FLIGHT_STATUSES = frozenset({"confirmed", "enqueued"})
# Rows completion may repoint: a written record or an in-flight revision; resolved_externally is not one.
REPOINT_CANDIDATE_STATUSES = frozenset({"written"}) | IN_FLIGHT_STATUSES


def is_repoint_pending(status, confirmation_source) -> bool:
    """A completion repoint owns this row: in flight (confirmed/enqueued) under the repoint source."""
    return status in IN_FLIGHT_STATUSES and confirmation_source == REPOINT_CONFIRMATION_SOURCE


def has_written_record(item) -> bool:
    """EITHER half of a written record (sync token or lines) counts."""
    return bool(getattr(item, "written_sync_token", None) or getattr(item, "written_lines", None))


@dataclass
class VendorDominantSubCostCodeForBills:
    top_sub_cost_code_id: int
    top_count: int
    second_count: int
    total_count: int
    public_id: Optional[str]
    number: Optional[str]
    name: Optional[str]


@dataclass
class VendorDominantProjectForBills:
    top_project_id: int
    top_count: int
    second_count: int
    total_count: int


@dataclass
class CodingSuggestion:
    id: Optional[int]
    public_id: Optional[str]
    row_version: Optional[str]
    target_kind: Optional[str]
    bill_id: Optional[int]
    bill_credit_id: Optional[int]
    expense_id: Optional[int]
    attachment_id: Optional[int]
    qbo_purchase_id: Optional[int]
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
    features_json: Optional[str]
    budget_check: Optional[str]
    budget_remaining: Optional[Decimal]
    route: Optional[str]
    model_version: Optional[str]
    confirmed_project_id: Optional[int]
    confirmed_sub_cost_code_id: Optional[int]
    confirmed_description: Optional[str]
    was_overridden: Optional[bool]
    confirmed_by_user_id: Optional[int]
    confirmed_at: Optional[str]
    confirmed_mixed_codes: Optional[bool]
    prefill_state: Optional[str]
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
    confirmation_source: Optional[str] = None  # 'review_submit' | 'confirm_endpoint' (U-652) | 'complete_repoint' (U-551b-f1)
    confirmation_seq: Optional[int] = None  # U-652c: bumped on each confirmation
    written_sync_token: Optional[str] = None  # U-551b-f1: SyncToken our last recode write produced
    written_lines_json: Optional[str] = None  # U-551b-f1: JSON of the lines that write coded
    repoint_targets_json: Optional[str] = None  # U-551b-f1: JSON of the coding completion approved for a repoint

    @property
    def repoint_targets(self) -> Optional[list]:
        """Parsed repoint_targets_json. ``None`` = absent or malformed (fail closed: any bad entry
        invalidates the WHOLE record); ``[]`` = an explicit empty revision (nothing to repoint)."""
        if not self.repoint_targets_json:
            return None
        try:
            parsed = json.loads(self.repoint_targets_json)
            if not isinstance(parsed, list):
                return None
            for entry in parsed:
                if not isinstance(entry, dict):
                    return None
                for key in ("qbo_line_id", "repoint_from_item_ref_value"):
                    value = entry.get(key)
                    if not isinstance(value, str) or not value:
                        return None
                scc = entry.get("sub_cost_code_id")
                if not isinstance(scc, int) or isinstance(scc, bool):
                    return None
                pid = entry.get("project_id")
                if pid is not None and (not isinstance(pid, int) or isinstance(pid, bool)):
                    return None
                desc = entry.get("description")
                if desc is not None and not isinstance(desc, str):
                    return None
            return parsed
        except Exception:  # incl. RecursionError on hostile depth
            return None

    @property
    def written_lines(self) -> list:
        """Parsed written_lines_json. Validates the WHOLE record: any malformed entry -> [] (fail closed)."""
        if not self.written_lines_json:
            return []
        try:
            parsed = json.loads(self.written_lines_json)
            if not isinstance(parsed, list):
                return []
            for entry in parsed:
                if not isinstance(entry, dict):
                    return []
                for key in ("qbo_line_id", "item_ref_value"):
                    value = entry.get(key)
                    if not isinstance(value, str) or not value:
                        return []
                scc = entry.get("sub_cost_code_id")
                if scc is not None and (not isinstance(scc, int) or isinstance(scc, bool)):
                    return []
            return parsed
        except Exception:  # incl. RecursionError on hostile depth
            return []

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    def to_dict(self) -> dict:
        data = asdict(self)
        if data.get("suggestion_confidence") is not None:
            data["suggestion_confidence"] = str(data["suggestion_confidence"])
        if data.get("budget_remaining") is not None:
            data["budget_remaining"] = str(data["budget_remaining"])
        return data
