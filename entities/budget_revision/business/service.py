# Python Standard Library Imports
import base64
import logging
from typing import Optional

# Local Imports
from entities.budget_revision.business.model import BudgetRevision, VALID_TYPES
from entities.budget_revision.persistence.repo import BudgetRevisionRepository
from shared.access import assert_can_access_project
from shared.authz import current_user_id

logger = logging.getLogger(__name__)


class BudgetRevisionService:
    """Lifecycle rules (single enforcement point — sprocs are generic):

    - type='original' (Rev 0) is created ONLY by BudgetService.create for a
      fresh budget — rejected here if the budget already has any revisions.
    - type='change_order' requires the parent Budget to be 'active' (draft
      budgets are edited via Rev 0, not change orders).
    - Approved revisions are IMMUTABLE: update/delete reject Status='approved'.
    - Originals are deletable only via the parent-budget delete cascade —
      BudgetService passes ``force_internal=True`` (see delete_by_public_id).
    - approve_by_public_id rejects originals (those are approved via budget
      activation, which validates line completeness) and enforces the same
      completeness rule for change orders: >=1 line, every line carrying
      sub_cost_code_id + amount + price.
    """

    def __init__(self, repo: Optional[BudgetRevisionRepository] = None):
        self.repo = repo or BudgetRevisionRepository()

    # ------------------------------------------------------------------
    # Lazy cross-service resolution (BillService pattern — avoids circular
    # imports: BudgetService.create calls back into this service for Rev 0).
    # ------------------------------------------------------------------

    def _resolve_budget(self, budget_public_id: str):
        from entities.budget.business.service import BudgetService

        budget = BudgetService().read_by_public_id(public_id=budget_public_id)
        if not budget:
            raise ValueError(f"Budget {budget_public_id!r} not found.")
        return budget

    def _assert_lines_complete(self, budget_revision_id: int) -> None:
        """Same completeness rule as budget activation: >=1 line and every
        line has sub_cost_code_id + amount + price non-null."""
        from entities.budget_line_item.business.service import BudgetLineItemService

        lines = BudgetLineItemService().read_by_budget_revision_id(budget_revision_id)
        if not lines:
            raise ValueError("Cannot approve a budget revision with no line items.")
        incomplete = [
            line
            for line in lines
            if line.sub_cost_code_id is None or line.amount is None or line.price is None
        ]
        if incomplete:
            raise ValueError(
                f"Cannot approve: {len(incomplete)} line item(s) are missing "
                f"a SubCostCode, Amount, or Price."
            )

    # ------------------------------------------------------------------
    # CRUD (METHOD_MAPPING contract: create / update_by_public_id /
    # delete_by_public_id — invoked via ProcessEngine instant workflows).
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        tenant_id: int = 1,
        budget_public_id: str,
        type: str = "change_order",
        title: Optional[str] = None,
        description: Optional[str] = None,
        effective_date: Optional[str] = None,
    ) -> BudgetRevision:
        if type not in VALID_TYPES:
            raise ValueError(f"Invalid revision type {type!r}. Must be one of {VALID_TYPES}.")

        budget = self._resolve_budget(budget_public_id)
        assert_can_access_project(budget.project_id)

        existing_revisions = self.repo.read_by_budget_id(int(budget.id))
        if type == "original" and existing_revisions:
            # 'original' is only valid when BudgetService.create calls us for
            # a fresh budget that has no Rev 0 yet.
            raise ValueError(
                "Budget already has revisions — an 'original' revision can only "
                "be created for a fresh budget. Create a 'change_order' instead."
            )
        if type == "change_order" and budget.status != "active":
            raise ValueError(
                "Change orders require an active budget — draft budgets are "
                "edited via Rev 0, not change orders."
            )

        return self.repo.create(
            budget_id=int(budget.id),
            type=type,
            title=title if title != "" else None,
            description=description if description != "" else None,
            effective_date=effective_date if effective_date != "" else None,
            created_by_user_id=current_user_id.get(),
        )

    def read_by_id(self, id: int) -> Optional[BudgetRevision]:
        item = self.repo.read_by_id(id)
        if item:
            assert_can_access_project(item.project_id)
        return item

    def read_by_public_id(self, public_id: str) -> Optional[BudgetRevision]:
        item = self.repo.read_by_public_id(public_id)
        if item:
            assert_can_access_project(item.project_id)
        return item

    def read_by_budget_id(self, budget_id: int) -> list[BudgetRevision]:
        """All revisions of a budget by internal id — used by BudgetService
        (delete cascade + activation's Rev 0 lookup). Asserts project access
        via the ProjectId the sproc's Budget join returned (defense-in-depth;
        budget-family callers have already asserted on the same project)."""
        items = self.repo.read_by_budget_id(int(budget_id))
        if items:
            assert_can_access_project(items[0].project_id)
        return items

    def read_by_budget_public_id(self, budget_public_id: str) -> list[BudgetRevision]:
        budget = self._resolve_budget(budget_public_id)
        assert_can_access_project(budget.project_id)
        return self.repo.read_by_budget_id(int(budget.id))

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        effective_date: Optional[str] = None,
    ) -> Optional[BudgetRevision]:
        """Notes-only header update — Title / Description / EffectiveDate are
        set UNCONDITIONALLY (clearable): callers must send the full row.
        Status never changes here; approval has its own path.
        """
        existing = self.repo.read_by_public_id(public_id)
        if not existing:
            return None
        assert_can_access_project(existing.project_id)

        if existing.status == "approved":
            raise ValueError("Approved budget revisions are immutable and cannot be updated.")

        if row_version is not None:
            existing.row_version = row_version

        updated = self.repo.update_by_id(
            id=int(existing.id),
            row_version=existing.row_version_bytes,
            title=title if title != "" else None,
            description=description if description != "" else None,
            effective_date=effective_date if effective_date != "" else None,
        )
        if updated is None:
            raise ValueError(
                "Concurrency conflict: BudgetRevision has been modified by another user."
            )
        return updated

    def delete_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str = None,
        force_internal: bool = False,
    ) -> Optional[BudgetRevision]:
        """Delete a draft revision (child BudgetLineItems are deleted in the
        same transaction by the sproc).

        ``force_internal`` is the parent-budget-delete escape hatch: when
        BudgetService deletes a whole Budget it calls
        ``delete_by_public_id(public_id, force_internal=True)`` per revision,
        which bypasses BOTH guards below (Type='original' and the
        approved-immutability rule) — the revisions must go regardless of
        state or the Budget delete would violate FK_BudgetRevision_Budget.
        External callers (router / agents) must never pass it.
        """
        existing = self.repo.read_by_public_id(public_id)
        if not existing:
            return None
        assert_can_access_project(existing.project_id)

        if not force_internal:
            if existing.status == "approved":
                raise ValueError("Approved budget revisions are immutable and cannot be deleted.")
            if existing.type == "original":
                raise ValueError(
                    "The original revision (Rev 0) cannot be deleted on its own — "
                    "it is removed when the parent budget is deleted."
                )

        if row_version is not None:
            existing.row_version = row_version

        logger.info(f"Deleting BudgetRevision {public_id} (id={existing.id})")
        deleted = self.repo.delete_by_id(int(existing.id), existing.row_version_bytes)
        if not deleted:
            raise ValueError(
                "Concurrency conflict: BudgetRevision has been modified by another user."
            )
        return existing

    # ------------------------------------------------------------------
    # Actions (direct service calls — TimeEntry approve precedent; no
    # Workflow row for the transition, accepted knowingly).
    # ------------------------------------------------------------------

    def approve_by_public_id(self, public_id: str, row_version: str) -> Optional[BudgetRevision]:
        """Approve a draft change order. row_version is REQUIRED — a body-less
        approve has a stale-read race. Returns None for an unknown public_id
        (router 404s, matching the activate path)."""
        if not row_version:
            raise ValueError("row_version is required to approve a budget revision.")

        existing = self.repo.read_by_public_id(public_id)
        if not existing:
            return None
        assert_can_access_project(existing.project_id)

        if existing.type == "original":
            raise ValueError(
                "Original revisions are approved via budget activation "
                "(POST /api/v1/activate/budget/{public_id}), not directly."
            )
        if existing.status == "approved":
            raise ValueError("BudgetRevision is already approved.")

        self._assert_lines_complete(int(existing.id))

        try:
            row_version_bytes = base64.b64decode(row_version)
        except Exception as error:
            raise ValueError(f"Invalid row_version: {error}") from error

        # Approval attribution must be a real actor — the sproc stamps
        # ApprovedByUserId verbatim (no COALESCE-to-system-default).
        approver_id = current_user_id.get()
        if approver_id is None:
            raise ValueError(
                "Cannot approve: no authenticated user in context to record "
                "as the approver."
            )

        approved = self.repo.approve_by_id(
            id=int(existing.id),
            row_version=row_version_bytes,
            approved_by_user_id=approver_id,
        )
        if approved is None:
            # Stale rowversion OR a concurrent approval flipped Status off
            # 'draft' — the sproc's WHERE clause covers both.
            raise ValueError(
                "Concurrency conflict: BudgetRevision was modified or approved "
                "by another user. Refresh and retry."
            )
        return approved
