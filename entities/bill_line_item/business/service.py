# Python Standard Library Imports
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from entities.bill_line_item.business.model import BillLineItem
from entities.bill_line_item.persistence.repo import BillLineItemRepository
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.project.business.service import ProjectService
from entities.bill.business.service import BillService


class BillLineItemService:
    """
    Service for BillLineItem entity business operations.
    """

    def __init__(self, repo: Optional[BillLineItemRepository] = None):
        """Initialize the BillLineItemService."""
        self.repo = repo or BillLineItemRepository()

    def create(self, *, tenant_id: int = None, bill_public_id: str, sub_cost_code_id: Optional[int] = None, project_public_id: Optional[str] = None, description: Optional[str] = None, quantity: Optional[int] = None, rate: Optional[Decimal] = None, amount: Optional[Decimal] = None, is_billable: Optional[bool] = None, is_billed: Optional[bool] = None, markup: Optional[Decimal] = None, price: Optional[Decimal] = None, is_draft: bool = True) -> BillLineItem:
        """
        Create a new bill line item.
        """
        # TODO: In Phase 10, use tenant_id for tenant isolation
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
        
        # Validate Project exists if provided and get internal ID
        project_id = None
        if project_public_id is not None:
            project = ProjectService().read_by_public_id(public_id=project_public_id)
            if not project:
                raise ValueError(f"Project with public_id '{project_public_id}' not found.")
            project_id = project.id
        
        return self.repo.create(
            bill_id=bill.id,
            sub_cost_code_id=sub_cost_code_id,
            project_id=project_id,
            description=description,
            quantity=quantity,
            rate=rate,
            amount=amount,
            is_billable=is_billable,
            is_billed=is_billed,
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

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        bill_public_id: str = None,
        sub_cost_code_id: int = None,
        project_public_id: str = None,
        description: str = None,
        quantity: int = None,
        rate: float = None,
        amount: float = None,
        is_billable: bool = None,
        is_billed: bool = None,
        markup: float = None,
        price: float = None,
        is_draft: bool = None,
    ) -> Optional[BillLineItem]:
        """
        Update a bill line item by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            
            # Validate Bill exists if provided (using public_id)
            if bill_public_id is not None:
                bill = BillService().read_by_public_id(public_id=bill_public_id)
                if not bill:
                    raise ValueError(f"Bill with public_id '{bill_public_id}' not found.")
                existing.bill_id = bill.id
            
            # Validate SubCostCode exists if provided (or allow None to clear the relationship)
            if sub_cost_code_id is not None:
                # Note: SubCostCodeService.read_by_id expects a string
                sub_cost_code = SubCostCodeService().read_by_id(id=str(sub_cost_code_id))
                if not sub_cost_code:
                    raise ValueError(f"SubCostCode with id '{sub_cost_code_id}' not found.")
                existing.sub_cost_code_id = sub_cost_code_id
            
            # Validate Project exists if provided (or allow None to clear the relationship)
            if project_public_id is not None:
                project = ProjectService().read_by_public_id(public_id=project_public_id)
                if not project:
                    raise ValueError(f"Project with public_id '{project_public_id}' not found.")
                existing.project_id = project.id
            
            # Update fields
            if description is not None:
                existing.description = description
            if quantity is not None:
                existing.quantity = quantity
            if rate is not None:
                existing.rate = Decimal(str(rate))
            if amount is not None:
                existing.amount = Decimal(str(amount))
            if is_billable is not None:
                existing.is_billable = is_billable
            if is_billed is not None:
                existing.is_billed = is_billed
            if markup is not None:
                existing.markup = Decimal(str(markup))
            if price is not None:
                existing.price = Decimal(str(price))
            if is_draft is not None:
                existing.is_draft = is_draft
            
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[BillLineItem]:
        """
        Delete a bill line item by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            from entities.invoice_line_item.persistence.repo import InvoiceLineItemRepository
            from entities.contract_labor.persistence.repo import ContractLaborRepository
            InvoiceLineItemRepository().delete_by_bill_line_item_id(existing.id)
            cl_repo = ContractLaborRepository()
            for cl_entry in cl_repo.read_by_bill_line_item_id(existing.id):
                cl_entry.bill_line_item_id = None
                cl_repo.update_by_id(cl_entry)
            return self.repo.delete_by_id(existing.id)
        return None
