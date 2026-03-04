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
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[BillCreditLineItem]:
        """
        Read a bill credit line item by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_bill_credit_id(self, bill_credit_id: int) -> list[BillCreditLineItem]:
        """
        Read all bill credit line items for a specific bill credit.
        """
        return self.repo.read_by_bill_credit_id(bill_credit_id)

    def update_by_public_id(
        self,
        public_id: str,
        bill_credit_line_item,  # BillCreditLineItemUpdate schema
    ) -> Optional[BillCreditLineItem]:
        """
        Update a bill credit line item by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None
        
        existing.row_version = bill_credit_line_item.row_version
        
        # Resolve bill_credit_public_id if provided
        if bill_credit_line_item.bill_credit_public_id:
            bill_credit = BillCreditService().read_by_public_id(public_id=bill_credit_line_item.bill_credit_public_id)
            if not bill_credit:
                raise ValueError(f"Bill credit with public_id '{bill_credit_line_item.bill_credit_public_id}' not found.")
            existing.bill_credit_id = bill_credit.id
        
        # Resolve project_public_id if provided
        if bill_credit_line_item.project_public_id is not None:
            if bill_credit_line_item.project_public_id:
                project = ProjectService().read_by_public_id(public_id=bill_credit_line_item.project_public_id)
                if not project:
                    raise ValueError(f"Project with public_id '{bill_credit_line_item.project_public_id}' not found.")
                existing.project_id = project.id
            else:
                existing.project_id = None
        
        if bill_credit_line_item.sub_cost_code_id is not None:
            existing.sub_cost_code_id = bill_credit_line_item.sub_cost_code_id
        if bill_credit_line_item.description is not None:
            existing.description = bill_credit_line_item.description
        if bill_credit_line_item.quantity is not None:
            existing.quantity = Decimal(str(bill_credit_line_item.quantity))
        if bill_credit_line_item.unit_price is not None:
            existing.unit_price = Decimal(str(bill_credit_line_item.unit_price))
        if bill_credit_line_item.amount is not None:
            existing.amount = Decimal(str(bill_credit_line_item.amount))
        if bill_credit_line_item.is_billable is not None:
            existing.is_billable = bill_credit_line_item.is_billable
        if hasattr(bill_credit_line_item, 'is_billed') and bill_credit_line_item.is_billed is not None:
            existing.is_billed = bill_credit_line_item.is_billed
        if bill_credit_line_item.billable_amount is not None:
            existing.billable_amount = Decimal(str(bill_credit_line_item.billable_amount))
        if bill_credit_line_item.is_draft is not None:
            existing.is_draft = bill_credit_line_item.is_draft
        
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str) -> Optional[BillCreditLineItem]:
        """
        Delete a bill credit line item by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if not existing or not existing.id:
            return None
        
        return self.repo.delete_by_id(existing.id)
