# Python Standard Library Imports
import logging
from typing import Optional

# Local Imports
from integrations.box.reconciliation.business.model import BoxReconciliationIssue
from integrations.box.reconciliation.persistence.repo import BoxReconciliationIssueRepository

logger = logging.getLogger(__name__)


class BoxReconciliationIssueService:
    """
    Thin service for flagging drift / dead-letter escalations in the Box
    integration layer. Primary caller today:

      1. Outbox worker dead-letter hook → `flag_dead_letter()`

    Kept deliberately simple for Phase 2 — no review-lifecycle mutations
    yet; a future Box reconciliation job will add drift writers (mirroring
    the MS Excel detector).
    """

    def __init__(self, repo: Optional[BoxReconciliationIssueRepository] = None):
        self.repo = repo or BoxReconciliationIssueRepository()

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
    ) -> BoxReconciliationIssue:
        """
        An outbox row dead-lettered. Severity `critical` for the upload kind
        (a silently-dropped document push means a project folder is missing
        a source document a human believes was filed), `high` otherwise.

        `tenant_id` carries the Box enterprise id (the column set mirrors
        `[ms].[ReconciliationIssue]` verbatim); `drive_item_id` carries the
        Box folder/file id involved.
        """
        # DriftType follows the kind namespace directly — easy to query.
        drift_type = f"{kind}_dead_letter" if len(f"{kind}_dead_letter") <= 32 else "outbox_dead_letter"
        severity = "critical" if kind in ("upload_box_file",) else "high"

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
            "box.reconcile.dead_letter.flagged",
            extra={
                "event_name": "box.reconcile.dead_letter.flagged",
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
