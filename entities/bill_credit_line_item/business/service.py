# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from entities.bill_credit_line_item.business.model import BillCreditLineItem
from entities.bill_credit_line_item.persistence.repo import BillCreditLineItemRepository
from entities.bill_credit.business.service import BillCreditService
from entities.project.business.service import ProjectService
from shared.access import assert_can_access_bill_credit
from shared.authz import current_user_id

logger = logging.getLogger(__name__)


class BillCreditLineItemService:
    """
    Service for BillCreditLineItem entity business operations.
    """

    def __init__(self, repo: Optional[BillCreditLineItemRepository] = None):
        """Initialize the BillCreditLineItemService."""
        self.repo = repo or BillCreditLineItemRepository()

    def create(
        self,
        *,
        bill_credit_public_id: str,
        sub_cost_code_id: Optional[int] = None,
        project_public_id: Optional[str] = None,
        description: Optional[str] = None,
        quantity: Optional[Decimal] = None,
        unit_price: Optional[Decimal] = None,
        amount: Optional[Decimal] = None,
        is_billable: Optional[bool] = None,
        is_billed: Optional[bool] = None,
        billable_amount: Optional[Decimal] = None,
        is_draft: bool = True,
    ) -> BillCreditLineItem:
        """
        Create a new bill credit line item.
        """
        if not bill_credit_public_id:
            raise ValueError("Bill credit is required.")
        
        # Resolve bill_credit_public_id to bill_credit_id
        bill_credit = BillCreditService().read_by_public_id(public_id=bill_credit_public_id)
        if not bill_credit:
            raise ValueError(f"Bill credit with public_id '{bill_credit_public_id}' not found.")
        bill_credit_id = bill_credit.id
        
        # Resolve project_public_id to project_id if provided
        project_id = None
        if project_public_id:
            project = ProjectService().read_by_public_id(public_id=project_public_id)
            if not project:
                raise ValueError(f"Project with public_id '{project_public_id}' not found.")
            project_id = project.id
        
        return self.repo.create(
            bill_credit_id=bill_credit_id,
            sub_cost_code_id=sub_cost_code_id,
            project_id=project_id,
            description=description,
            quantity=quantity,
            unit_price=unit_price,
            amount=amount,
            is_billable=is_billable,
            is_billed=is_billed,
            billable_amount=billable_amount,
            is_draft=is_draft,
            created_by_user_id=current_user_id.get(),
        )

    def read_all(self) -> list[BillCreditLineItem]:
        """
        Read all bill credit line items.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[BillCreditLineItem]:
        """
        Read a bill credit line item by ID.
        """
        line_item = self.repo.read_by_id(id)
        if line_item is None:
            return None
        assert_can_access_bill_credit(line_item.bill_credit_id)
        return line_item

    def read_by_public_id(self, public_id: str) -> Optional[BillCreditLineItem]:
        """
        Read a bill credit line item by public ID.
        """
        line_item = self.repo.read_by_public_id(public_id)
        if line_item is None:
            return None
        assert_can_access_bill_credit(line_item.bill_credit_id)
        return line_item

    def read_by_bill_credit_id(self, bill_credit_id: int) -> list[BillCreditLineItem]:
        """
        Read all bill credit line items for a specific bill credit.
        """
        assert_can_access_bill_credit(bill_credit_id)
        return self.repo.read_by_bill_credit_id(bill_credit_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        bill_credit_public_id: str = None,
        sub_cost_code_id: int = None,
        project_public_id: str = None,
        description: str = None,
        quantity: float = None,
        unit_price: float = None,
        amount: float = None,
        is_billable: bool = None,
        is_billed: bool = None,
        billable_amount: float = None,
        is_draft: bool = None,
    ) -> Optional[BillCreditLineItem]:
        """
        Update a bill credit line item by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        existing.row_version = row_version

        if bill_credit_public_id:
            bill_credit = BillCreditService().read_by_public_id(public_id=bill_credit_public_id)
            if not bill_credit:
                raise ValueError(f"Bill credit with public_id '{bill_credit_public_id}' not found.")
            existing.bill_credit_id = bill_credit.id

        if project_public_id is not None:
            if project_public_id:
                project = ProjectService().read_by_public_id(public_id=project_public_id)
                if not project:
                    raise ValueError(f"Project with public_id '{project_public_id}' not found.")
                existing.project_id = project.id
            else:
                existing.project_id = None

        if sub_cost_code_id is not None:
            existing.sub_cost_code_id = sub_cost_code_id
        if description is not None:
            existing.description = description
        if quantity is not None:
            existing.quantity = Decimal(str(quantity))
        if unit_price is not None:
            existing.unit_price = Decimal(str(unit_price))
        if amount is not None:
            existing.amount = Decimal(str(amount))
        if is_billable is not None:
            existing.is_billable = is_billable
        if is_billed is not None:
            existing.is_billed = is_billed
        if billable_amount is not None:
            existing.billable_amount = Decimal(str(billable_amount))
        if is_draft is not None:
            existing.is_draft = is_draft

        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str) -> Optional[BillCreditLineItem]:
        """
        Delete a bill credit line item by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if not existing or not existing.id:
            return None
        
        return self.repo.delete_by_id(existing.id)
