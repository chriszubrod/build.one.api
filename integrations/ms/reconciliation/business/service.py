# Python Standard Library Imports
import logging
from typing import Optional

# Local Imports
from integrations.ms.reconciliation.business.model import MsReconciliationIssue
from integrations.ms.reconciliation.persistence.repo import MsReconciliationIssueRepository

logger = logging.getLogger(__name__)

# Successfully resolved audit tenant only — never cache ``unknown`` or failures.
_memoized_audit_tenant_id: Optional[str] = None


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

    def flag_project_mapping_missing(
        self,
        *,
        entity_type: str,
        entity_public_id: str,
        project_id: int,
        mapping_kind: str,
        details: str,
        tenant_id: Optional[str] = None,
        reconcile_run_id: Optional[str] = None,
    ) -> MsReconciliationIssue:
        """
        Bill/Expense/BillCredit completion could not file to SharePoint or Excel
        because the project has no mapping for that integration surface.
        Severity `high` — recoverable once the project is mapped and the step
        is re-driven; `critical` stays reserved for dead-letter.

        When ``tenant_id`` is omitted, it is resolved from the stored MsAuth
        record. If none can be resolved, the literal ``unknown`` is stored so
        audit rows stay visibly unattributed rather than blank.
        """
        if tenant_id is None:
            tenant_id = self._resolve_tenant_id_for_audit()
        if not tenant_id:
            tenant_id = "unknown"
        enriched_details = (
            f"project_id={project_id}; mapping_kind={mapping_kind}; {details}"
        )
        issue = self.repo.create(
            drift_type="project_mapping_missing",
            severity="high",
            action="flagged",
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            tenant_id=tenant_id,
            details=enriched_details,
            reconcile_run_id=reconcile_run_id,
        )
        logger.warning(
            "ms.reconcile.drift.flagged",
            extra={
                "event_name": "ms.reconcile.drift.flagged",
                "drift_type": "project_mapping_missing",
                "severity": "high",
                "entity_type": entity_type,
                "entity_public_id": entity_public_id,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "mapping_kind": mapping_kind,
                "issue_public_id": issue.public_id,
            },
        )
        return issue

    @staticmethod
    def _resolve_tenant_id_for_audit() -> Optional[str]:
        """
        TenantId-only read off ``ms.Auth`` — no token hydration or decrypt.

        Memoized per process when a single unambiguous tenant is resolved; ``unknown``
        and transient failures are not cached.
        """
        global _memoized_audit_tenant_id
        if _memoized_audit_tenant_id is not None:
            return _memoized_audit_tenant_id
        try:
            from integrations.ms.auth.persistence.repo import MsAuthRepository

            tenant_id = MsAuthRepository().read_tenant_id()
            if tenant_id:
                _memoized_audit_tenant_id = tenant_id
            return tenant_id
        except Exception:
            logger.exception("ms.reconcile.tenant_resolve_failed")
            return None

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
        from integrations.ms.outbox.business.service import EXCEL_WRITING_KINDS  # lazy: outbox imports this module
        severity = "critical" if kind in EXCEL_WRITING_KINDS else "high"

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
