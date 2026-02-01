# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.bill_credit_line_item_attachment.business.model import BillCreditLineItemAttachment
from entities.bill_credit_line_item_attachment.persistence.repo import BillCreditLineItemAttachmentRepository
from entities.bill_credit_line_item.business.service import BillCreditLineItemService
from entities.attachment.business.service import AttachmentService


class BillCreditLineItemAttachmentService:
    """
    Service for BillCreditLineItemAttachment entity business operations.
    """

    def __init__(self, repo: Optional[BillCreditLineItemAttachmentRepository] = None):
        """Initialize the BillCreditLineItemAttachmentService."""
        self.repo = repo or BillCreditLineItemAttachmentRepository()

    def create(self, *, tenant_id: int = None, bill_credit_line_item_public_id: str, attachment_public_id: str) -> BillCreditLineItemAttachment:
        """
        Create a new bill credit line item attachment link.
        
        Ensures 1-1 relationship: Each BillCreditLineItem can have only ONE attachment.
        If a link already exists for this BillCreditLineItem, returns the existing record
        instead of creating a duplicate.
        
        Args:
            bill_credit_line_item_public_id: Public ID of the bill credit line item
            attachment_public_id: Public ID of the attachment
            
        Returns:
            BillCreditLineItemAttachment: The existing record if duplicate, or newly created record
            
        Raises:
            ValueError: If bill credit line item or attachment not found
        """
        # TODO: In Phase 10, use tenant_id for tenant isolation
        # Resolve public IDs to internal IDs
        bill_credit_line_item = BillCreditLineItemService().read_by_public_id(public_id=bill_credit_line_item_public_id)
        attachment = AttachmentService().read_by_public_id(public_id=attachment_public_id)
        
        if not bill_credit_line_item or not bill_credit_line_item.id:
            raise ValueError(f"BillCreditLineItem with public_id '{bill_credit_line_item_public_id}' not found")
        if not attachment or not attachment.id:
            raise ValueError(f"Attachment with public_id '{attachment_public_id}' not found")
        
        bill_credit_line_item_id = int(bill_credit_line_item.id)
        attachment_id = int(attachment.id)
        
        # Check if a BillCreditLineItemAttachment already exists for this BillCreditLineItem (1-1 relationship)
        existing = self.repo.read_by_bill_credit_line_item_id(bill_credit_line_item_id=bill_credit_line_item_id)
        if existing:
            # A record already exists for this BillCreditLineItem
            # Return the existing record instead of creating a duplicate
            return existing
        
        # No existing attachment for this BillCreditLineItem - safe to create
        return self.repo.create(bill_credit_line_item_id=bill_credit_line_item_id, attachment_id=attachment_id)

    def read_all(self) -> list[BillCreditLineItemAttachment]:
        """
        Read all bill credit line item attachments.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[BillCreditLineItemAttachment]:
        """
        Read a bill credit line item attachment by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[BillCreditLineItemAttachment]:
        """
        Read a bill credit line item attachment by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_bill_credit_line_item_id(self, bill_credit_line_item_public_id: str) -> Optional[BillCreditLineItemAttachment]:
        """
        Read bill credit line item attachment by bill credit line item public ID.
        Returns the single attachment for the bill credit line item (1-1 relationship).
        """
        bill_credit_line_item = BillCreditLineItemService().read_by_public_id(public_id=bill_credit_line_item_public_id)
        if not bill_credit_line_item or not bill_credit_line_item.id:
            return None
        
        bill_credit_line_item_id = int(bill_credit_line_item.id)
        return self.repo.read_by_bill_credit_line_item_id(bill_credit_line_item_id=bill_credit_line_item_id)

    def read_by_bill_credit_line_item_ids(self, bill_credit_line_item_public_ids: list[str]) -> list[BillCreditLineItemAttachment]:
        """
        Read bill credit line item attachments for multiple bill credit line items in a single query.
        Returns list of attachments for the given bill credit line item public IDs.
        """
        if not bill_credit_line_item_public_ids:
            return []
        return self.repo.read_by_bill_credit_line_item_public_ids(bill_credit_line_item_public_ids)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[BillCreditLineItemAttachment]:
        """
        Delete a bill credit line item attachment by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing and existing.id:
            return self.repo.delete_by_id(existing.id)
        return None
