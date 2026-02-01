# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.expense_line_item_attachment.business.model import ExpenseLineItemAttachment
from entities.expense_line_item_attachment.persistence.repo import ExpenseLineItemAttachmentRepository
from entities.expense_line_item.business.service import ExpenseLineItemService
from entities.attachment.business.service import AttachmentService


class ExpenseLineItemAttachmentService:
    """
    Service for ExpenseLineItemAttachment entity business operations.
    """

    def __init__(self, repo: Optional[ExpenseLineItemAttachmentRepository] = None):
        """Initialize the ExpenseLineItemAttachmentService."""
        self.repo = repo or ExpenseLineItemAttachmentRepository()

    def create(self, *, tenant_id: int = None, expense_line_item_public_id: str, attachment_public_id: str) -> ExpenseLineItemAttachment:
        """
        Create a new expense line item attachment link.
        
        Ensures 1-1 relationship: Each ExpenseLineItem can have only ONE attachment.
        If a link already exists for this ExpenseLineItem, returns the existing record
        instead of creating a duplicate.
        
        Args:
            expense_line_item_public_id: Public ID of the expense line item
            attachment_public_id: Public ID of the attachment
            
        Returns:
            ExpenseLineItemAttachment: The existing record if duplicate, or newly created record
            
        Raises:
            ValueError: If expense line item or attachment not found
        """
        # TODO: In Phase 10, use tenant_id for tenant isolation
        # Resolve public IDs to internal IDs
        expense_line_item = ExpenseLineItemService().read_by_public_id(public_id=expense_line_item_public_id)
        attachment = AttachmentService().read_by_public_id(public_id=attachment_public_id)
        
        if not expense_line_item or not expense_line_item.id:
            raise ValueError(f"ExpenseLineItem with public_id '{expense_line_item_public_id}' not found")
        if not attachment or not attachment.id:
            raise ValueError(f"Attachment with public_id '{attachment_public_id}' not found")
        
        expense_line_item_id = int(expense_line_item.id)
        attachment_id = int(attachment.id)
        
        # Check if an ExpenseLineItemAttachment already exists for this ExpenseLineItem (1-1 relationship)
        existing = self.repo.read_by_expense_line_item_id(expense_line_item_id=expense_line_item_id)
        if existing:
            # A record already exists for this ExpenseLineItem
            # Return the existing record instead of creating a duplicate
            return existing
        
        # No existing attachment for this ExpenseLineItem - safe to create
        return self.repo.create(expense_line_item_id=expense_line_item_id, attachment_id=attachment_id)

    def read_all(self) -> list[ExpenseLineItemAttachment]:
        """
        Read all expense line item attachments.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[ExpenseLineItemAttachment]:
        """
        Read an expense line item attachment by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[ExpenseLineItemAttachment]:
        """
        Read an expense line item attachment by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_expense_line_item_id(self, expense_line_item_public_id: str) -> Optional[ExpenseLineItemAttachment]:
        """
        Read expense line item attachment by expense line item public ID.
        Returns the single attachment for the expense line item (1-1 relationship).
        """
        expense_line_item = ExpenseLineItemService().read_by_public_id(public_id=expense_line_item_public_id)
        if not expense_line_item or not expense_line_item.id:
            return None
        
        expense_line_item_id = int(expense_line_item.id)
        return self.repo.read_by_expense_line_item_id(expense_line_item_id=expense_line_item_id)

    def read_by_expense_line_item_ids(self, expense_line_item_public_ids: list[str]) -> list[ExpenseLineItemAttachment]:
        """
        Read expense line item attachments for multiple expense line items in a single query.
        Returns list of attachments for the given expense line item public IDs.
        """
        if not expense_line_item_public_ids:
            return []
        return self.repo.read_by_expense_line_item_public_ids(expense_line_item_public_ids)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[ExpenseLineItemAttachment]:
        """
        Delete an expense line item attachment by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing and existing.id:
            return self.repo.delete_by_id(existing.id)
        return None
