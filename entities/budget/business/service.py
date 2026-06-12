# Python Standard Library Imports
import base64
import logging
import re
import uuid
from decimal import Decimal
from typing import Optional

# Local Imports
from entities.budget.business.model import Budget
from entities.budget.persistence.repo import BudgetRepository
from shared.access import assert_can_access_project
from shared.authz import current_user_id, current_is_system_admin

logger = logging.getLogger(__name__)


def _snake(name: str) -> str:
    """PascalCase sproc column → snake_case payload key (BillCost → bill_cost)."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


class BudgetService:
    """
    Budget lifecycle enforcement point.

    The sprocs are generic; every rule lives here:
      - create auto-creates Rev 0 ('original', draft) and pre-checks the
        one-live-budget-per-project rule (UQ_Budget_ProjectId_Active is the
        DB backstop).
      - update is Notes-only — Status never changes via update.
      - delete only when Status='draft'; revisions (each deleting its own
        line items) are deleted first, explicitly.
      - activate requires Status='draft', a nonempty Rev 0 whose every line
        carries sub_cost_code_id + amount + price, then runs the single-txn
        ActivateBudgetById (budget→active, Rev 0→approved).
    """

    def __init__(self, repo: Optional[BudgetRepository] = None):
        self.repo = repo or BudgetRepository()

    # ------------------------------------------------------------------
    # Lazy cross-service accessors (BillService pattern — avoids circular
    # imports between budget / budget_revision / budget_line_item).
    # ------------------------------------------------------------------

    @property
    def budget_revision_service(self):
        from entities.budget_revision.business.service import BudgetRevisionService
        return BudgetRevisionService()

    @property
    def budget_line_item_service(self):
        from entities.budget_line_item.business.service import BudgetLineItemService
        return BudgetLineItemService()

    # ------------------------------------------------------------------
    # CRUD (METHOD_MAPPING contract: create / update_by_public_id /
    # delete_by_public_id)
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        tenant_id: int = 1,
        project_public_id: str,
        notes: Optional[str] = None,
    ) -> dict:
        from entities.project.business.service import ProjectService

        project = ProjectService().read_by_public_id(public_id=project_public_id)
        if not project:
            raise ValueError(f"Project {project_public_id!r} not found.")
        project_id = int(project.id)

        assert_can_access_project(project_id)

        # Friendly pre-check — UQ_Budget_ProjectId_Active is the backstop.
        existing = self.repo.read_by_project_id(project_id)
        if existing:
            raise ValueError(
                f"A budget already exists for this project "
                f"(status {existing.status!r}, public_id {existing.public_id}). "
                f"Archive it before creating a new one."
            )

        try:
            budget = self.repo.create(
                project_id=project_id,
                notes=notes if notes else None,
                created_by_user_id=current_user_id.get(),
            )
        except Exception as e:
            # Create race: two simultaneous creates can both pass the
            # pre-check; the filtered unique index rejects the loser. Re-raise
            # as the same friendly error instead of a raw duplicate-key 500.
            if "UQ_Budget_ProjectId_Active" in str(e) or "duplicate key" in str(e).lower():
                raise ValueError(
                    "A budget already exists for this project. "
                    "Archive it before creating a new one."
                )
            raise

        # Auto-create Rev 0 — the 'original' schedule of values (draft).
        # Compensating delete on failure: a Budget without Rev 0 can never
        # be activated, so don't leave the orphan behind.
        try:
            revision = self.budget_revision_service.create(
                budget_public_id=str(budget.public_id),
                type="original",
            )
        except Exception:
            try:
                self.repo.delete_by_id(int(budget.id), budget.row_version_bytes)
            except Exception as cleanup_error:
                logger.error(
                    f"Failed to clean up Budget {budget.public_id} after "
                    f"Rev 0 creation failure: {cleanup_error}"
                )
            raise

        data = budget.to_dict()
        data["original_revision"] = (
            revision.to_dict() if hasattr(revision, "to_dict") else revision
        )
        return data

    def read_all(self) -> list[Budget]:
        return self.repo.read_all(
            actor_user_id=current_user_id.get(),
            actor_is_system_admin=current_is_system_admin.get(),
        )

    def read_all_with_rollups(self) -> list[dict]:
        """List rows enriched with contract_value / drawn_price /
        remaining_to_draw (Decimal → str for transport). Both reads share the
        same fail-closed actor scoping, so the merge can't widen visibility."""
        actor_user_id = current_user_id.get()
        actor_is_system_admin = current_is_system_admin.get()
        budgets = self.repo.read_all(
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
        )
        rollups = self.repo.read_list_rollups(
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
        )
        rows = []
        for budget in budgets:
            data = budget.to_dict()
            rollup = rollups.get(int(budget.id))
            for key in ("contract_value", "drawn_price", "remaining_to_draw"):
                value = rollup.get(key) if rollup else None
                data[key] = str(value) if value is not None else "0.00"
            rows.append(data)
        return rows

    def variance_by_public_id(self, public_id: str) -> Optional[dict]:
        """Budget-vs-actual-vs-drawn payload: SCC-grain rows + CostCode
        subtotals + grand totals, all server-computed in Decimal (the web
        layer must never do currency math — JS Number drift). Returns None
        for an unknown budget (router 404s)."""
        budget = self.read_by_public_id(public_id=public_id)  # asserts access
        if budget is None:
            return None

        raw_rows = self.repo.read_variance_by_project_id(
            int(budget.project_id),
            budget_id=int(budget.id),
            actor_user_id=current_user_id.get(),
            actor_is_system_admin=current_is_system_admin.get(),
        )

        money_fields = (
            "BudgetAmount", "BudgetPrice", "BillCost", "ExpenseCost",
            "BillCreditCost", "ContractLaborCost", "EmployeeLaborCost",
            "ActualCost", "DrawnPrice", "RemainingToDraw", "CostVariance",
        )

        def _zero_acc():
            # 0.00 (not 0) so empty-payload totals serialize as "0.00",
            # matching the sproc's DECIMAL(18,2) string shape.
            acc = {field: Decimal("0.00") for field in money_fields}
            acc["UnpricedLaborHours"] = Decimal("0.00")
            return acc

        rows = []
        cost_code_accs: dict = {}   # cost_code_id (None = Uncategorized) → acc
        totals = _zero_acc()

        for raw in raw_rows:
            row = {
                "sub_cost_code_id": raw["SubCostCodeId"],
                "sub_cost_code_number": raw["SubCostCodeNumber"],
                "sub_cost_code_name": raw["SubCostCodeName"],
                "cost_code_id": raw["CostCodeId"],
                "cost_code_number": raw["CostCodeNumber"],
                "cost_code_name": raw["CostCodeName"],
            }
            for field in money_fields:
                value = raw[field] if raw[field] is not None else Decimal("0")
                row[_snake(field)] = str(value)
                totals[field] += value
            unpriced = (
                raw["UnpricedLaborHours"]
                if raw["UnpricedLaborHours"] is not None else Decimal("0")
            )
            row["unpriced_labor_hours"] = str(unpriced)
            totals["UnpricedLaborHours"] += unpriced
            rows.append(row)

            cc_key = raw["CostCodeId"]
            acc = cost_code_accs.setdefault(cc_key, _zero_acc())
            acc["_number"] = raw["CostCodeNumber"]
            acc["_name"] = raw["CostCodeName"]
            for field in money_fields:
                acc[field] += raw[field] if raw[field] is not None else Decimal("0")
            acc["UnpricedLaborHours"] += unpriced

        cost_codes = []
        for cc_id, acc in cost_code_accs.items():
            entry = {
                "cost_code_id": cc_id,
                "cost_code_number": acc.get("_number"),
                "cost_code_name": acc.get("_name") or ("Uncategorized" if cc_id is None else None),
                "uncategorized": cc_id is None,
            }
            for field in money_fields:
                entry[_snake(field)] = str(acc[field])
            entry["unpriced_labor_hours"] = str(acc["UnpricedLaborHours"])
            cost_codes.append(entry)
        # Uncategorized last, then by cost code number (server-side sort —
        # the SCC rows already arrive sorted from the sproc).
        cost_codes.sort(key=lambda e: (e["uncategorized"], e["cost_code_number"] or ""))

        totals_out = {_snake(field): str(totals[field]) for field in money_fields}
        totals_out["unpriced_labor_hours"] = str(totals["UnpricedLaborHours"])

        return {
            "budget": budget.to_dict(),
            "rows": rows,
            "cost_codes": cost_codes,
            "totals": totals_out,
        }

    def read_by_id(self, id: int) -> Optional[Budget]:
        budget = self.repo.read_by_id(id)
        if budget is None:
            return None
        assert_can_access_project(budget.project_id)
        return budget

    def read_by_public_id(self, public_id: str) -> Optional[Budget]:
        # Non-GUID input would raise a SQL conversion error (500) at the
        # UNIQUEIDENTIFIER param — treat malformed ids as not-found instead.
        try:
            uuid.UUID(str(public_id))
        except (ValueError, AttributeError, TypeError):
            return None
        budget = self.repo.read_by_public_id(public_id)
        if budget is None:
            return None
        assert_can_access_project(budget.project_id)
        return budget

    def read_by_project_public_id(self, project_public_id: str) -> Optional[Budget]:
        """The live (non-archived) budget for a project, if one exists."""
        from entities.project.business.service import ProjectService

        project = ProjectService().read_by_public_id(public_id=project_public_id)
        if not project:
            raise ValueError(f"Project {project_public_id!r} not found.")
        project_id = int(project.id)
        assert_can_access_project(project_id)
        return self.repo.read_by_project_id(project_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Optional[Budget]:
        """Notes only — status NEVER changes via update (activate owns it).

        notes=None preserves the existing value; notes='' clears it (house
        free-text convention). The sproc's @Notes SET is unconditional; the
        preserve-vs-clear resolution happens here.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        if row_version is not None:
            existing.row_version = row_version

        if notes is not None:
            existing.notes = notes if notes != "" else None

        updated = self.repo.update_by_id(existing)
        if updated is None:
            raise ValueError(
                "Concurrency conflict: Budget has been modified by another user."
            )
        return updated

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[Budget]:
        """Draft-only delete. Revisions (each deleting its own line items)
        go first — explicit child deletes, no FK cascade."""
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        if existing.status != "draft":
            raise ValueError(
                f"Only draft budgets can be deleted "
                f"(this budget is {existing.status!r})."
            )

        revision_service = self.budget_revision_service
        for revision in revision_service.read_by_budget_id(int(existing.id)):
            # force_internal: the parent-budget-delete escape hatch — bypasses
            # the Type='original' guard (every budget has a Rev 0) and the
            # approved-immutability guard so the cascade can always complete.
            revision_service.delete_by_public_id(
                public_id=str(revision.public_id),
                force_internal=True,
            )

        logger.info(f"Deleting Budget {public_id} (id={existing.id})")
        deleted = self.repo.delete_by_id(int(existing.id), existing.row_version_bytes)
        if not deleted:
            raise ValueError(
                "Concurrency conflict: Budget was modified by another user during delete."
            )
        return existing

    # ------------------------------------------------------------------
    # Actions (direct service call from the router — TimeEntry precedent)
    # ------------------------------------------------------------------

    def activate_by_public_id(self, public_id: str, row_version: str) -> Optional[Budget]:
        """draft → active. Validates Rev 0 is nonempty and every line is
        complete (sub_cost_code_id + amount + price non-null), then runs the
        single-txn ActivateBudgetById which also approves Rev 0.

        Returns None when the budget doesn't exist (router 404s). Raises
        ValueError on state/validation/concurrency problems — concurrency
        messages contain 'Concurrency' so raise_workflow_error maps to 409.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        if not row_version:
            raise ValueError("row_version is required to activate a budget.")

        if existing.status != "draft":
            raise ValueError(
                f"Only draft budgets can be activated "
                f"(this budget is {existing.status!r})."
            )

        revisions = self.budget_revision_service.read_by_budget_id(int(existing.id))
        rev0 = next(
            (r for r in revisions if getattr(r, "type", None) == "original"),
            None,
        )
        if rev0 is None:
            raise ValueError(
                "Budget has no original revision (Rev 0) — nothing to activate."
            )

        lines = self.budget_line_item_service.read_by_budget_revision_id(int(rev0.id))
        if not lines:
            raise ValueError(
                "Cannot activate: the original revision has no line items."
            )

        incomplete: list[str] = []
        for index, line in enumerate(lines, start=1):
            missing = [
                field_name
                for field_name, value in (
                    ("sub_cost_code", line.sub_cost_code_id),
                    ("amount", line.amount),
                    ("price", line.price),
                )
                if value is None
            ]
            if missing:
                incomplete.append(
                    f"line {index} ({line.public_id}) is missing {', '.join(missing)}"
                )
        if incomplete:
            raise ValueError(
                "Cannot activate: every line item needs a sub cost code, "
                "amount, and price — " + "; ".join(incomplete) + "."
            )

        try:
            row_version_bytes = base64.b64decode(row_version)
        except Exception as error:
            raise ValueError(f"Invalid row_version: {error}") from error

        # Approval attribution must be a real actor — the sproc no longer
        # COALESCEs to the system default for ApprovedByUserId.
        approver_id = current_user_id.get()
        if approver_id is None:
            raise ValueError(
                "Cannot activate: no authenticated user in context to record "
                "as the approver."
            )

        activated = self.repo.activate_by_id(
            int(existing.id),
            row_version_bytes,
            approved_by_user_id=approver_id,
        )
        if activated is None:
            raise ValueError(
                "Concurrency conflict: Budget was modified or already activated "
                "by another user. Refresh and retry."
            )
        return activated
