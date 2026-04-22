# Python Standard Library Imports
import logging
from typing import Optional

# Local Imports
from integrations.ms.reconciliation.business.model import MsReconciliationIssue
from integrations.ms.reconciliation.persistence.repo import MsReconciliationIssueRepository

logger = logging.getLogger(__name__)


class MsReconciliationIssueService:
    """
    Thin service for flagging drift / dead-letter escalations in the MS
    integration layer. Two common callers:

      1. Excel reconciliation job → `flag_excel_row_missing()`
      2. Outbox worker dead-letter hook → `flag_dead_letter()`

    Kept deliberately simple for Phase 3 — no review-lifecycle mutations yet.
    """

    def __init__(self, repo: Optional[MsReconciliationIssueRepository] = None):
        self.repo = repo or MsReconciliationIssueRepository()

    def flag_excel_row_missing(
        self,
        *,
        entity_type: str,
        entity_public_id: str,
        tenant_id: str,
        drive_item_id: str,
        worksheet_name: str,
        details: str,
        reconcile_run_id: Optional[str] = None,
    ) -> MsReconciliationIssue:
        """
        Completed bill/expense has no matching row in its Excel workbook.
        Severity `high` (not critical — the reconciliation job detects it
        days after the fact; critical is reserved for dead-letter).
        """
        issue = self.repo.create(
            drift_type="excel_row_missing",
            severity="high",
            action="flagged",
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            tenant_id=tenant_id,
            drive_item_id=drive_item_id,
            worksheet_name=worksheet_name,
            details=details,
            reconcile_run_id=reconcile_run_id,
        )
        logger.warning(
            "ms.reconcile.drift.flagged",
            extra={
                "event_name": "ms.reconcile.drift.flagged",
                "drift_type": "excel_row_missing",
                "severity": "high",
                "entity_type": entity_type,
                "entity_public_id": entity_public_id,
                "tenant_id": tenant_id,
                "drive_item_id": drive_item_id,
                "worksheet_name": worksheet_name,
                "issue_public_id": issue.public_id,
            },
        )
        return issue

    def flag_dead_letter(
        self,
        *,
        kind: str,
        entity_type: str,
        entity_public_id: str,
        tenant_id: str,
        outbox_public_id: str,
        details: str,
        drive_item_id: Optional[str] = None,
        worksheet_name: Optional[str] = None,
    ) -> MsReconciliationIssue:
        """
        An outbox row dead-lettered. Severity `critical` for Excel kinds
        (per the user requirement that failed Excel writes must be followed
        up), `high` otherwise.
        """
        # DriftType follows the kind namespace directly — easy to query.
        drift_type = f"{kind}_dead_letter" if len(f"{kind}_dead_letter") <= 32 else "outbox_dead_letter"
        severity = "critical" if kind in ("append_excel_row", "insert_excel_row") else "high"

        issue = self.repo.create(
            drift_type=drift_type,
            severity=severity,
            action="flagged",
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            tenant_id=tenant_id,
            drive_item_id=drive_item_id,
            worksheet_name=worksheet_name,
            outbox_public_id=outbox_public_id,
            details=details,
        )
        logger.error(
            "ms.reconcile.dead_letter.flagged",
            extra={
                "event_name": "ms.reconcile.dead_letter.flagged",
                "drift_type": drift_type,
                "severity": severity,
                "kind": kind,
                "entity_type": entity_type,
                "entity_public_id": entity_public_id,
                "tenant_id": tenant_id,
                "outbox_public_id": outbox_public_id,
                "issue_public_id": issue.public_id,
            },
        )
        return issue
