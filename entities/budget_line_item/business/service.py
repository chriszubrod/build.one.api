# Python Standard Library Imports
import logging
from decimal import Decimal, InvalidOperation
from typing import Optional, Union

# Local Imports
from entities.budget_line_item.business.model import (
    BudgetLineItem,
    REVISION_STATUS_APPROVED,
)
from entities.budget_line_item.persistence.repo import BudgetLineItemRepository
from shared.access import assert_can_access_project
from shared.authz import current_user_id

logger = logging.getLogger(__name__)


def _coerce_decimal(value: Union[str, Decimal, int, float, None]) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"Invalid decimal value: {value!r}") from e


class BudgetLineItemService:
    """
    Service for BudgetLineItem business operations.

    THE child-lock enforcement point: line items belonging to an
    APPROVED BudgetRevision are immutable (one rule everywhere —
    approved revisions are immutable; this covers Rev 0 of an active
    budget too, since activation approves it). Router RBAC cannot
    express this, and the sprocs are deliberately generic, so every
    mutation here checks the parent revision status first.
    """

    def __init__(self, repo: Optional[BudgetLineItemRepository] = None):
        self.repo = repo or BudgetLineItemRepository()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_revision_by_public_id(self, budget_revision_public_id: str):
        """Lazy cross-service import (BillService pattern) to avoid circular deps."""
        from entities.budget_revision.business.service import BudgetRevisionService

        return BudgetRevisionService().read_by_public_id(public_id=budget_revision_public_id)

    @staticmethod
    def _assert_revision_mutable(revision_status: Optional[str], action: str) -> None:
        if revision_status == REVISION_STATUS_APPROVED:
            raise ValueError(
                f"Cannot {action}: the parent budget revision is approved. "
                "Approved revisions are immutable."
            )

    @staticmethod
    def _validate_sub_cost_code(sub_cost_code_id: Optional[int]) -> None:
        if sub_cost_code_id is None:
            return
        from entities.sub_cost_code.business.service import SubCostCodeService

        sub_cost_code = SubCostCodeService().read_by_id(id=str(sub_cost_code_id))
        if not sub_cost_code:
            raise ValueError(f"SubCostCode with id '{sub_cost_code_id}' not found.")

    # ------------------------------------------------------------------
    # CRUD (METHOD_MAPPING contract: create / update_by_public_id /
    # delete_by_public_id)
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        tenant_id: int = None,
        budget_revision_public_id: str,
        sub_cost_code_id: Optional[int] = None,
        description: Optional[str] = None,
        quantity: Union[str, Decimal, None] = None,
        rate: Union[str, Decimal, None] = None,
        amount: Union[str, Decimal, None] = None,
        markup: Union[str, Decimal, None] = None,
        price: Union[str, Decimal, None] = None,
    ) -> BudgetLineItem:
        """
        Create a budget line item under a DRAFT revision. All business
        fields are nullable (auto-save grid persists partial rows);
        negative values are legal (CO deltas). Amount/Price are
        client-sent — no derived computation server-side in v1.
        """
        revision = self._read_revision_by_public_id(budget_revision_public_id)
        if not revision:
            raise ValueError(
                f"BudgetRevision with public_id '{budget_revision_public_id}' not found."
            )

        assert_can_access_project(revision.project_id)
        self._assert_revision_mutable(revision.status, "add a line item")
        self._validate_sub_cost_code(sub_cost_code_id)

        return self.repo.create(
            budget_revision_id=int(revision.id),
            sub_cost_code_id=sub_cost_code_id,
            description=description,
            quantity=_coerce_decimal(quantity),
            rate=_coerce_decimal(rate),
            amount=_coerce_decimal(amount),
            markup=_coerce_decimal(markup),
            price=_coerce_decimal(price),
            created_by_user_id=current_user_id.get(),
        )

    def read_by_id(self, id: int) -> Optional[BudgetLineItem]:
        item = self.repo.read_by_id(id)
        if item is None:
            return None
        assert_can_access_project(item.project_id)
        return item

    def read_by_public_id(self, public_id: str) -> Optional[BudgetLineItem]:
        item = self.repo.read_by_public_id(public_id)
        if item is None:
            return None
        assert_can_access_project(item.project_id)
        return item

    def read_by_budget_revision_id(self, budget_revision_id: int) -> list[BudgetLineItem]:
        items = self.repo.read_by_budget_revision_id(budget_revision_id)
        if items:
            # Every row in the set shares the same Budget → ProjectId.
            assert_can_access_project(items[0].project_id)
        return items

    def read_by_budget_revision_public_id(
        self, budget_revision_public_id: str
    ) -> Optional[list[BudgetLineItem]]:
        """
        List line items for a revision by its public_id. Returns None
        when the revision itself does not exist (router maps to 404);
        an empty list means the revision exists but has no lines yet.
        """
        revision = self._read_revision_by_public_id(budget_revision_public_id)
        if not revision:
            return None
        assert_can_access_project(revision.project_id)
        return self.repo.read_by_budget_revision_id(int(revision.id))

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str = None,
        sub_cost_code_id: Optional[int] = None,
        description: Optional[str] = None,
        quantity: Union[str, Decimal, None] = None,
        rate: Union[str, Decimal, None] = None,
        amount: Union[str, Decimal, None] = None,
        markup: Union[str, Decimal, None] = None,
        price: Union[str, Decimal, None] = None,
    ) -> Optional[BudgetLineItem]:
        """
        Unconditional SET of all business fields (grid clearability) —
        the client sends the full row state on every save and None
        clears the column. Rejected when the parent revision is
        approved.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        self._assert_revision_mutable(existing.revision_status, "update this line item")
        self._validate_sub_cost_code(sub_cost_code_id)

        if row_version is not None:
            existing.row_version = row_version

        return self.repo.update_by_id(
            id=int(existing.id),
            row_version_bytes=existing.row_version_bytes,
            sub_cost_code_id=sub_cost_code_id,
            description=description,
            quantity=_coerce_decimal(quantity),
            rate=_coerce_decimal(rate),
            amount=_coerce_decimal(amount),
            markup=_coerce_decimal(markup),
            price=_coerce_decimal(price),
        )

    def delete_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str = None,
    ) -> Optional[BudgetLineItem]:
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        self._assert_revision_mutable(existing.revision_status, "delete this line item")

        if row_version is not None:
            existing.row_version = row_version

        logger.info(f"Deleting BudgetLineItem {public_id} (id={existing.id})")
        self.repo.delete_by_id(int(existing.id), existing.row_version_bytes)
        return existing
