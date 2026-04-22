# Python Standard Library Imports
from dataclasses import dataclass
from typing import Optional


@dataclass
class MsReconciliationIssue:
    """
    Row in `[ms].[ReconciliationIssue]` — drift or failure finding in the MS
    integration layer (SharePoint, Excel, Mail).

    Two primary writers:
      1. Excel reconciliation job: `DriftType='excel_row_missing'`
         when a completed bill has no matching row in its project workbook.
      2. Outbox worker dead-letter hook: `DriftType='excel_write_dead_letter'`
         (etc.) when a Kind-specific dead-letter needs operator follow-up.

    Each issue has a Status lifecycle (`open` → `acknowledged` → `resolved`)
    for a future review UI.
    """

    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None

    drift_type: Optional[str] = None
    severity: Optional[str] = None
    action: Optional[str] = None

    entity_type: Optional[str] = None
    entity_public_id: Optional[str] = None
    tenant_id: Optional[str] = None

    drive_item_id: Optional[str] = None
    worksheet_name: Optional[str] = None
    outbox_public_id: Optional[str] = None

    details: Optional[str] = None
    status: Optional[str] = None
    acknowledged_at: Optional[str] = None
    resolved_at: Optional[str] = None

    reconcile_run_id: Optional[str] = None
