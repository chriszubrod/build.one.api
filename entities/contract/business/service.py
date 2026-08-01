# Python Standard Library Imports
from decimal import Decimal, InvalidOperation
from typing import Optional, Union

# Local Imports
from entities.contract.business.model import Contract
from entities.contract.persistence.repo import ContractRepository
from shared.access import assert_can_access_project
from shared.authz import current_user_id


def _coerce_decimal(value: Union[str, Decimal, int, float, None]) -> Optional[Decimal]:
    """Money in as Decimal(str(value)) — never float (float round-trips corrupt)."""
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"Invalid decimal value: {value!r}") from e


class ContractService:
    """
    Service for Contract entity business operations.

    MINIMAL BY DESIGN: BuildersFeeRate is the only business field. The full
    contract model (contract value, change orders, retainage, dates, and the
    relationship to the existing Budget entity) is deferred to a formal design
    conversation.

    Lean instant-workflow CRUD — the ProcessEngine dispatches
    create / update_by_public_id (METHOD_MAPPING). Delete is intentionally not
    exposed until the model is designed.

    Per-row access control mirrors the sibling project-linked services
    (Invoice / Budget): every path gates on `assert_can_access_project` against
    the Contract's ProjectId (system admins bypass; a non-accessible project
    raises EntityNotAccessibleError → HTTP 404 so the URL doesn't confirm the
    row exists).
    """

    def __init__(self, repo: Optional[ContractRepository] = None):
        self.repo = repo or ContractRepository()

    def create(
        self,
        *,
        tenant_id: int = 1,
        project_id: int,
        builders_fee_rate: Union[str, Decimal, None] = None,
    ) -> Contract:
        """
        Create a Contract for a Project. BuildersFeeRate coerces to Decimal
        (a fraction — 0.10 = 10%). CreatedByUserId falls back to
        COALESCE(@CreatedByUserId, 17) in the sproc for system context.

        Gated: the actor must be able to access the target Project before insert.
        """
        assert_can_access_project(project_id)
        return self.repo.create(
            project_id=project_id,
            builders_fee_rate=_coerce_decimal(builders_fee_rate),
            created_by_user_id=current_user_id.get(),
        )

    def read_by_public_id(self, public_id: str) -> Optional[Contract]:
        contract = self.repo.read_by_public_id(public_id)
        if contract is None:
            return None
        assert_can_access_project(contract.project_id)
        return contract

    def read_by_project_id(self, project_id: int) -> list[Contract]:
        # Gate on the requested project (mirrors BudgetService.read_by_project_*).
        assert_can_access_project(project_id)
        return self.repo.read_by_project_id(project_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        builders_fee_rate: Union[str, Decimal, None] = None,
    ) -> Optional[Contract]:
        """
        Set the fee rate (ROWVERSION-guarded; the sproc CASE-WHEN preserves it
        when NULL is passed). Access is enforced by the read_by_public_id prefetch.
        A stale RowVersion matches no row (empty result) → surface as a
        concurrency conflict (HTTP 409 via raise_workflow_error), NOT a 404.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        existing.row_version = row_version
        if builders_fee_rate is not None:
            existing.builders_fee_rate = _coerce_decimal(builders_fee_rate)

        updated = self.repo.update_by_public_id(existing)
        if updated is None:
            raise ValueError(
                "Concurrency conflict: Contract has been modified by another user."
            )
        return updated
