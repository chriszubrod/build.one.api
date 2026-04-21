# Python Standard Library Imports
from dataclasses import dataclass
from typing import Optional


@dataclass
class ReconciliationIssue:
    """
    A drift finding written by the reconciliation job.
    See `[qbo].[ReconciliationIssue]` for the full column contract.
    """

    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None

    drift_type: Optional[str] = None          # qbo_missing_locally, local_missing_qbo, etc.
    severity: Optional[str] = None            # low | medium | high
    action: Optional[str] = None              # auto_fixed | flagged

    entity_type: Optional[str] = None         # Bill | Invoice | Purchase | VendorCredit
    entity_public_id: Optional[str] = None
    qbo_id: Optional[str] = None
    realm_id: Optional[str] = None

    details: Optional[str] = None

    status: Optional[str] = None              # open | acknowledged | resolved
    acknowledged_at: Optional[str] = None
    resolved_at: Optional[str] = None

    reconcile_run_id: Optional[str] = None
