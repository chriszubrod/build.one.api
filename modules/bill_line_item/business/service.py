# Python Standard Library Imports
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from modules.bill_line_item.business.model import BillLineItem
from modules.bill_line_item.persistence.repo import BillLineItemRepository
from modules.sub_cost_code.business.service import SubCostCodeService
from modules.bill.business.service import BillService


class BillLineItemService:
    """
    Service for BillLineItem entity business operations.
    """

    def __init__(self, repo: Optional[BillLineItemRepository] = None):
        """Initialize the BillLineItemService."""
        self.repo = repo or BillLineItemRepository()

    def create(self, *, bill_public_id: str, sub_cost_code_id: Optional[int] = None, description: Optional[str] = None, quantity: Optional[int] = None, rate: Optional[Decimal] = None, amount: Optional[Decimal] = None, is_billable: Optional[bool] = None, markup: Optional[Decimal] = None, price: Optional[Decimal] = None, is_draft: bool = True) -> BillLineItem:
        """
        Create a new bill line item.
        """
        # Validate Bill exists and get internal ID
        bill = BillService().read_by_public_id(public_id=bill_public_id)
        if not bill:
            raise ValueError(f"Bill with public_id '{bill_public_id}' not found.")
        
        # Validate SubCostCode exists if provided
        if sub_cost_code_id is not None:
            # Note: SubCostCodeService.read_by_id expects a string
            sub_cost_code = SubCostCodeService().read_by_id(id=str(sub_cost_code_id))
            if not sub_cost_code:
                raise ValueError(f"SubCostCode with id '{sub_cost_code_id}' not found.")
        
        return self.repo.create(
            bill_id=bill.id,
            sub_cost_code_id=sub_cost_code_id,
            description=description,
            quantity=quantity,
            rate=rate,
            amount=amount,
            is_billable=is_billable,
            markup=markup,
            price=price,
            is_draft=is_draft,
        )

    def read_all(self) -> list[BillLineItem]:
        """
        Read all bill line items.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[BillLineItem]:
        """
        Read a bill line item by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[BillLineItem]:
        """
        Read a bill line item by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_bill_id(self, bill_id: int) -> list[BillLineItem]:
        """
        Read all bill line items for a specific bill.
        """
        return self.repo.read_by_bill_id(bill_id=bill_id)

    def update_by_public_id(self, public_id: str, bill_line_item) -> Optional[BillLineItem]:
        """
        Update a bill line item by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = bill_line_item.row_version
            
            # Validate Bill exists if provided (using public_id)
            if hasattr(bill_line_item, 'bill_public_id') and bill_line_item.bill_public_id is not None:
                bill = BillService().read_by_public_id(public_id=bill_line_item.bill_public_id)
                if not bill:
                    raise ValueError(f"Bill with public_id '{bill_line_item.bill_public_id}' not found.")
                existing.bill_id = bill.id
            
            # Validate SubCostCode exists if provided (or allow None to clear the relationship)
            if hasattr(bill_line_item, 'sub_cost_code_id'):
                if bill_line_item.sub_cost_code_id is not None:
                    # Note: SubCostCodeService.read_by_id expects a string
                    sub_cost_code = SubCostCodeService().read_by_id(id=str(bill_line_item.sub_cost_code_id))
                    if not sub_cost_code:
                        raise ValueError(f"SubCostCode with id '{bill_line_item.sub_cost_code_id}' not found.")
                existing.sub_cost_code_id = bill_line_item.sub_cost_code_id
            
            # Update fields
            if hasattr(bill_line_item, 'description'):
                existing.description = bill_line_item.description
            if hasattr(bill_line_item, 'quantity'):
                existing.quantity = bill_line_item.quantity
            if hasattr(bill_line_item, 'rate'):
                existing.rate = bill_line_item.rate
            if hasattr(bill_line_item, 'amount'):
                existing.amount = bill_line_item.amount
            if hasattr(bill_line_item, 'is_billable'):
                existing.is_billable = bill_line_item.is_billable
            if hasattr(bill_line_item, 'markup'):
                existing.markup = bill_line_item.markup
            if hasattr(bill_line_item, 'price'):
                existing.price = bill_line_item.price
            if hasattr(bill_line_item, 'is_draft') and bill_line_item.is_draft is not None:
                existing.is_draft = bill_line_item.is_draft
            
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str) -> Optional[BillLineItem]:
        """
        Delete a bill line item by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
