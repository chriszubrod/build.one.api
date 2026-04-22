# Python Standard Library Imports
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Set

# Local Imports
from integrations.ms.base.errors import MsGraphError
from integrations.ms.base.logger import get_ms_logger
from integrations.ms.reconciliation.business.service import MsReconciliationIssueService

logger = get_ms_logger(__name__)


# Lookback window: only inspect bills/expenses completed in the last N days.
# Prevents the daily job from re-scanning the full history. Bills older than
# this should have been caught by a previous run; if they weren't, the user
# will have noticed and manually addressed. 30 days is comfortably larger
# than the typical review cycle.
DEFAULT_LOOKBACK_DAYS = 30


class ExcelMissingRowDetector:
    """
    Daily reconciliation: for each project with a linked Excel workbook, find
    bills/expenses completed in the lookback window that have no matching
    row in the workbook. Flags each finding as a ReconciliationIssue with
    `DriftType='excel_row_missing'`, `Severity='high'`, `Action='flagged'`.

    Phase 3 scope is narrow per Round 0 decision: detect missing rows only.
    Value drift (row exists with different values) and duplicate rows
    (same public_id appears twice) are deferred to Phase 4+.
    """

    def __init__(
        self,
        *,
        issue_service: Optional[MsReconciliationIssueService] = None,
    ):
        self.issue_service = issue_service or MsReconciliationIssueService()

    def run(self, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> dict:
        """
        Run the detector across all projects with Excel mappings. Returns a
        summary dict suitable for logging; each finding is persisted as a
        ReconciliationIssue row.
        """
        run_id = str(uuid.uuid4())
        since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        logger.info(
            "ms.reconcile.excel.run.started",
            extra={
                "event_name": "ms.reconcile.excel.run.started",
                "operation_name": "ms.reconcile.excel",
                "reconcile_run_id": run_id,
                "lookback_days": lookback_days,
                "since": since.isoformat(),
            },
        )

        tenant_id = self._resolve_tenant_id()
        if not tenant_id:
            logger.warning("ms.reconcile.excel.run.skipped: no tenant_id available")
            return {"run_id": run_id, "projects_checked": 0, "issues_flagged": 0, "skipped": True}

        project_public_ids = self._candidate_projects(since=since)
        logger.info(
            f"ms.reconcile.excel.run: {len(project_public_ids)} project(s) have "
            f"completed bills/expenses since {since.isoformat()}"
        )

        issues_flagged = 0
        projects_checked = 0
        for project_public_id in project_public_ids:
            try:
                flagged = self._reconcile_one_project(
                    project_public_id=project_public_id,
                    tenant_id=tenant_id,
                    since=since,
                    run_id=run_id,
                )
                issues_flagged += flagged
                projects_checked += 1
            except Exception:
                logger.exception(
                    "ms.reconcile.excel.project_failed",
                    extra={
                        "event_name": "ms.reconcile.excel.project_failed",
                        "project_public_id": project_public_id,
                        "reconcile_run_id": run_id,
                    },
                )

        summary = {
            "run_id": run_id,
            "projects_checked": projects_checked,
            "issues_flagged": issues_flagged,
        }
        logger.info(
            "ms.reconcile.excel.run.completed",
            extra={
                "event_name": "ms.reconcile.excel.run.completed",
                "operation_name": "ms.reconcile.excel",
                "reconcile_run_id": run_id,
                **summary,
            },
        )
        return summary

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolve_tenant_id() -> Optional[str]:
        from integrations.ms.auth.business.service import MsAuthService

        try:
            auth = MsAuthService().ensure_valid_token()
            return auth.tenant_id if auth else None
        except Exception:
            logger.exception("ms.reconcile.excel.tenant_resolve_failed")
            return None

    @staticmethod
    def _candidate_projects(since: datetime) -> List[str]:
        """
        Find projects that have at least one completed (is_draft=False)
        bill or expense with at least one line item in the lookback window.
        Returns project public_ids.

        Implemented as a minimum-viable query: scan completed bills/expenses,
        collect distinct project public_ids. For larger installations this
        should become a sproc, but at ~500/month volume the straight scan
        is fine.
        """
        from entities.bill.business.service import BillService
        from entities.expense.business.service import ExpenseService
        from entities.project.business.service import ProjectService

        project_ids: Set[int] = set()

        try:
            bills = BillService().read_all()
            for bill in bills:
                if getattr(bill, "is_draft", True):
                    continue
                if not bill.modified_datetime:
                    continue
                try:
                    bill_mtime = datetime.strptime(bill.modified_datetime, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                if bill_mtime < since:
                    continue
                # Collect project ids from the bill's line items.
                try:
                    from entities.bill_line_item.business.service import BillLineItemService
                except ImportError:
                    # Fall back to BillService if line item service lives there.
                    BillLineItemService = None
                line_items = None
                if BillLineItemService is not None:
                    line_items = BillLineItemService().read_by_bill_id(bill_id=int(bill.id))
                if line_items:
                    for li in line_items:
                        if getattr(li, "project_id", None):
                            project_ids.add(int(li.project_id))
        except Exception:
            logger.exception("ms.reconcile.excel.candidate_bills_failed")

        try:
            expenses = ExpenseService().read_all()
            for expense in expenses:
                if getattr(expense, "is_draft", True):
                    continue
                if not expense.modified_datetime:
                    continue
                try:
                    ex_mtime = datetime.strptime(expense.modified_datetime, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                if ex_mtime < since:
                    continue
                try:
                    from entities.expense_line_item.business.service import ExpenseLineItemService
                except ImportError:
                    ExpenseLineItemService = None
                if ExpenseLineItemService is not None:
                    line_items = ExpenseLineItemService().read_by_expense_id(expense_id=int(expense.id))
                    if line_items:
                        for li in line_items:
                            if getattr(li, "project_id", None):
                                project_ids.add(int(li.project_id))
        except Exception:
            logger.exception("ms.reconcile.excel.candidate_expenses_failed")

        # Resolve project internal ids → public_ids.
        project_public_ids: List[str] = []
        project_service = ProjectService()
        for pid in project_ids:
            try:
                project = project_service.read_by_id(id=str(pid))
                if project and project.public_id:
                    project_public_ids.append(str(project.public_id))
            except Exception:
                logger.exception(f"ms.reconcile.excel.project_lookup_failed: {pid}")

        return project_public_ids

    def _reconcile_one_project(
        self,
        *,
        project_public_id: str,
        tenant_id: str,
        since: datetime,
        run_id: str,
    ) -> int:
        """
        Check one project's Excel workbook for missing rows corresponding to
        completed bill/expense line items. Returns the number of issues flagged.
        """
        from entities.project.business.service import ProjectService
        from entities.bill_line_item.business.service import BillLineItemService
        from entities.expense_line_item.business.service import ExpenseLineItemService
        from entities.bill.business.service import BillService
        from entities.expense.business.service import ExpenseService
        from integrations.ms.sharepoint.external.client import get_excel_used_range_values
        from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository
        from integrations.ms.sharepoint.driveitem.persistence.repo import MsDriveItemRepository
        from integrations.ms.sharepoint.driveitem.connector.project_excel.business.service import (
            ProjectExcelConnector,
        )

        project = ProjectService().read_by_public_id(public_id=project_public_id)
        if not project:
            return 0

        excel_mapping = ProjectExcelConnector().get_excel_for_project(project_id=int(project.id))
        if not excel_mapping:
            return 0

        worksheet_name = excel_mapping.get("worksheet_name")
        driveitem_id_local = excel_mapping.get("id")
        if not worksheet_name or not driveitem_id_local:
            return 0

        driveitem = next(
            (d for d in MsDriveItemRepository().read_all() if d.id == driveitem_id_local),
            None,
        )
        if not driveitem:
            return 0

        drive = MsDriveRepository().read_by_id(driveitem.ms_drive_id)
        if not drive:
            return 0

        try:
            worksheet_result = get_excel_used_range_values(
                drive_id=drive.drive_id,
                item_id=driveitem.item_id,
                worksheet_name=worksheet_name,
            )
        except MsGraphError as error:
            logger.warning(
                f"ms.reconcile.excel.workbook_read_failed: {error}",
                extra={
                    "event_name": "ms.reconcile.excel.workbook_read_failed",
                    "project_public_id": project_public_id,
                    "drive_item_id": driveitem.item_id,
                    "reconcile_run_id": run_id,
                },
            )
            return 0

        if worksheet_result.get("status_code") != 200:
            logger.warning(
                f"ms.reconcile.excel.workbook_read_not_ok: "
                f"{worksheet_result.get('message')}",
                extra={
                    "event_name": "ms.reconcile.excel.workbook_read_not_ok",
                    "project_public_id": project_public_id,
                    "drive_item_id": driveitem.item_id,
                    "reconcile_run_id": run_id,
                },
            )
            return 0

        range_data = worksheet_result.get("range", {}) or {}
        rows = range_data.get("values", []) or []
        existing_public_ids: Set[str] = set()
        for row in rows:
            if len(row) > 25:  # column Z (index 25) is the reconciliation key
                val = row[25]
                if val is not None and str(val).strip():
                    existing_public_ids.add(str(val).strip())

        # Expected line items: completed bills/expenses with this project,
        # modified within the lookback window.
        expected_pairs = []  # (entity_type, entity_public_id, line_item_public_id)

        bill_service = BillService()
        for bill in bill_service.read_all():
            if getattr(bill, "is_draft", True):
                continue
            if not bill.modified_datetime:
                continue
            try:
                mtime = datetime.strptime(bill.modified_datetime, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if mtime < since:
                continue
            line_items = BillLineItemService().read_by_bill_id(bill_id=int(bill.id))
            for li in line_items or []:
                if getattr(li, "project_id", None) and int(li.project_id) == int(project.id):
                    if li.public_id:
                        expected_pairs.append(("Bill", str(bill.public_id), str(li.public_id)))

        expense_service = ExpenseService()
        for expense in expense_service.read_all():
            if getattr(expense, "is_draft", True):
                continue
            if not expense.modified_datetime:
                continue
            try:
                mtime = datetime.strptime(expense.modified_datetime, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if mtime < since:
                continue
            line_items = ExpenseLineItemService().read_by_expense_id(expense_id=int(expense.id))
            for li in line_items or []:
                if getattr(li, "project_id", None) and int(li.project_id) == int(project.id):
                    if li.public_id:
                        expected_pairs.append(("Expense", str(expense.public_id), str(li.public_id)))

        flagged = 0
        for entity_type, entity_public_id, line_item_public_id in expected_pairs:
            if line_item_public_id not in existing_public_ids:
                self.issue_service.flag_excel_row_missing(
                    entity_type=entity_type,
                    entity_public_id=entity_public_id,
                    tenant_id=tenant_id,
                    drive_item_id=driveitem.item_id,
                    worksheet_name=worksheet_name,
                    details=(
                        f"Expected {entity_type} line_item public_id "
                        f"{line_item_public_id} in worksheet '{worksheet_name}' "
                        f"of project '{project.name}' but not found."
                    ),
                    reconcile_run_id=run_id,
                )
                flagged += 1

        return flagged
