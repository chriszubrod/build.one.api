# Python Standard Library Imports
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from entities.expense_line_item.business.model import ExpenseLineItem
from entities.expense_line_item.persistence.repo import ExpenseLineItemRepository
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.project.business.service import ProjectService
from entities.expense.business.service import ExpenseService


class ExpenseLineItemService:
    """
    Service for ExpenseLineItem entity business operations.
    """

    def __init__(self, repo: Optional[ExpenseLineItemRepository] = None):
        """Initialize the ExpenseLineItemService."""
        self.repo = repo or ExpenseLineItemRepository()

    def create(self, *, tenant_id: int = None, expense_public_id: str, sub_cost_code_id: Optional[int] = None, project_public_id: Optional[str] = None, description: Optional[str] = None, quantity: Optional[int] = None, rate: Optional[Decimal] = None, amount: Optional[Decimal] = None, is_billable: Optional[bool] = None, is_billed: Optional[bool] = None, markup: Optional[Decimal] = None, price: Optional[Decimal] = None, is_draft: bool = True) -> ExpenseLineItem:
        """
        Create a new expense line item.
        """
        # TODO: In Phase 10, use tenant_id for tenant isolation
        # Validate Expense exists and get internal ID
        expense = ExpenseService().read_by_public_id(public_id=expense_public_id)
        if not expense:
            raise ValueError(f"Expense with public_id '{expense_public_id}' not found.")
        
        # Validate SubCostCode exists if provided
        if sub_cost_code_id is not None:
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
            expense_id=expense.id,
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

    def read_all(self) -> list[ExpenseLineItem]:
        """
        Read all expense line items.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[ExpenseLineItem]:
        """
        Read an expense line item by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[ExpenseLineItem]:
        """
        Read an expense line item by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_expense_id(self, expense_id: int) -> list[ExpenseLineItem]:
        """
        Read all expense line items for a specific expense.
        """
        return self.repo.read_by_expense_id(expense_id=expense_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        expense_public_id: str = None,
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
    ) -> Optional[ExpenseLineItem]:
        """
        Update an expense line item by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            
            # Validate Expense exists if provided (using public_id)
            if expense_public_id is not None:
                expense = ExpenseService().read_by_public_id(public_id=expense_public_id)
                if not expense:
                    raise ValueError(f"Expense with public_id '{expense_public_id}' not found.")
                existing.expense_id = expense.id
            
            # Validate SubCostCode exists if provided (or allow None to clear the relationship)
            if sub_cost_code_id is not None:
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

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[ExpenseLineItem]:
        """
        Delete an expense line item by public ID, cascading to its attachment.

        Process:
        1. Find the ExpenseLineItemAttachment link for this line item
        2. If found: delete the Attachment record + Azure blob, then delete the link
        3. Delete the ExpenseLineItem record
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        # Step 1-2: Clean up attachment (1-1 relationship)
        try:
            from entities.expense_line_item_attachment.persistence.repo import ExpenseLineItemAttachmentRepository
            from entities.attachment.business.service import AttachmentService
            from shared.storage import AzureBlobStorage, AzureBlobStorageError

            attachment_link = ExpenseLineItemAttachmentRepository().read_by_expense_line_item_id(
                expense_line_item_id=existing.id
            )
            if attachment_link:
                attachment_service = AttachmentService()
                attachment = attachment_service.read_by_id(id=attachment_link.attachment_id) if attachment_link.attachment_id else None
                if attachment:
                    if attachment.blob_url:
                        try:
                            AzureBlobStorage().delete_file(attachment.blob_url)
                        except Exception as e:
                            import logging
                            logging.getLogger(__name__).warning(
                                "Could not delete blob %s for attachment %s: %s",
                                attachment.blob_url, attachment.id, e,
                            )
                    try:
                        attachment_service.delete_by_public_id(public_id=attachment.public_id)
                    except Exception as e:
                        import logging
                        logging.getLogger(__name__).warning(
                            "Could not delete attachment record %s: %s", attachment.id, e
                        )
                try:
                    ExpenseLineItemAttachmentRepository().delete_by_id(id=attachment_link.id)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(
                        "Could not delete ExpenseLineItemAttachment %s: %s", attachment_link.id, e
                    )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "Error during attachment cleanup for ExpenseLineItem %s: %s", existing.id, e
            )

        # Step 3: Delete the line item
        return self.repo.delete_by_id(existing.id)
