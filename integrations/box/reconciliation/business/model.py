# Python Standard Library Imports
from dataclasses import dataclass
from typing import Optional


@dataclass
class BoxReconciliationIssue:
    """
    Row in `[box].[ReconciliationIssue]` — drift or failure finding in the
    Box integration layer.

    Primary writer today is the outbox worker's dead-letter hook:
    `DriftType='upload_box_file_dead_letter'` (etc.) when a Kind-specific
    dead-letter needs operator follow-up. A future Box reconciliation job
    (mirroring the MS Excel detector) will add drift-type writers.

    The column set mirrors `[ms].[ReconciliationIssue]` verbatim so a future
    consolidated review UI can query both tables with uniform shape —
    `tenant_id` carries the Box enterprise id; `drive_item_id` carries the
    Box folder/file id involved.

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
