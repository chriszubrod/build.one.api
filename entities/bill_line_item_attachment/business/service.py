# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.bill_line_item_attachment.business.model import BillLineItemAttachment
from entities.bill_line_item_attachment.persistence.repo import BillLineItemAttachmentRepository
from entities.bill_line_item.business.service import BillLineItemService
from entities.attachment.business.service import AttachmentService
from shared.authz import current_user_id, current_is_system_admin


class BillLineItemAttachmentService:
    """
    Service for BillLineItemAttachment entity business operations.
    """

    def __init__(self, repo: Optional[BillLineItemAttachmentRepository] = None):
        """Initialize the BillLineItemAttachmentService."""
        self.repo = repo or BillLineItemAttachmentRepository()

    def create(self, *, tenant_id: int = None, bill_line_item_public_id: str, attachment_public_id: str) -> BillLineItemAttachment:
        """
        Create a new bill line item attachment link.
        
        Ensures 1-1 relationship: Each BillLineItem can have only ONE attachment.
        If a link already exists for this BillLineItem, returns the existing record
        instead of creating a duplicate.
        
        Args:
            bill_line_item_public_id: Public ID of the bill line item
            attachment_public_id: Public ID of the attachment
            
        Returns:
            BillLineItemAttachment: The existing record if duplicate, or newly created record
            
        Raises:
            ValueError: If bill line item or attachment not found
        """
        # TODO: In Phase 10, use tenant_id for tenant isolation
        # Resolve public IDs to internal IDs
        bill_line_item = BillLineItemService().read_by_public_id(public_id=bill_line_item_public_id)
        attachment = AttachmentService().read_by_public_id(public_id=attachment_public_id)
        
        if not bill_line_item or not bill_line_item.id:
            raise ValueError(f"BillLineItem with public_id '{bill_line_item_public_id}' not found")
        if not attachment or not attachment.id:
            raise ValueError(f"Attachment with public_id '{attachment_public_id}' not found")
        
        bill_line_item_id = int(bill_line_item.id)
        attachment_id = int(attachment.id)
        
        # Check if a BillLineItemAttachment already exists for this BillLineItem (1-1 relationship)
        existing = self.repo.read_by_bill_line_item_id(bill_line_item_id=bill_line_item_id)
        if existing:
            # A record already exists for this BillLineItem
            # Return the existing record instead of creating a duplicate
            return existing
        
        # No existing attachment for this BillLineItem - safe to create
        return self.repo.create(bill_line_item_id=bill_line_item_id, attachment_id=attachment_id, created_by_user_id=current_user_id.get())

    def read_all(self) -> list[BillLineItemAttachment]:
        """
        Read bill line item attachments, scoped by UserProject for non-admin
        actors. Scoping happens in the sproc, via the parent BillLineItem's Bill.
        """
        return self.repo.read_all(
            actor_user_id=current_user_id.get(),
            actor_is_system_admin=current_is_system_admin.get(),
        )

    def read_by_id(self, id: int) -> Optional[BillLineItemAttachment]:
        """
        Read a bill line item attachment by ID, gated on the parent Bill.
        """
        link = self.repo.read_by_id(id)
        return self._gated(link)

    def read_by_public_id(self, public_id: str) -> Optional[BillLineItemAttachment]:
        """
        Read a bill line item attachment by public ID, gated on the parent Bill.
        """
        link = self.repo.read_by_public_id(public_id)
        return self._gated(link)

    def _gated(
        self, link: Optional[BillLineItemAttachment]
    ) -> Optional[BillLineItemAttachment]:
        """Assert the caller may see this link's parent Bill, then return it.

        Single-row reads gate here rather than in the sproc — the same split
        `read_all` (sproc-scoped) vs `read_by_*` (service-gated) that Bill and
        BillLineItem already use. Resolving the line item goes through
        BillLineItemService, which itself asserts on the parent Bill, so an
        inaccessible link raises EntityNotAccessibleError → 404, never 403.
        """
        if link is None or link.bill_line_item_id is None:
            return link
        BillLineItemService().read_by_id(int(link.bill_line_item_id))
        return link

    def read_by_bill_line_item_id(self, bill_line_item_public_id: str) -> Optional[BillLineItemAttachment]:
        """
        Read bill line item attachment by bill line item public ID.
        Returns the single attachment for the bill line item (1-1 relationship).
        """
        bill_line_item = BillLineItemService().read_by_public_id(public_id=bill_line_item_public_id)
        if not bill_line_item or not bill_line_item.id:
            return None
        
        bill_line_item_id = int(bill_line_item.id)
        return self.repo.read_by_bill_line_item_id(bill_line_item_id=bill_line_item_id)

    def read_by_bill_line_item_ids(self, bill_line_item_public_ids: list[str]) -> list[BillLineItemAttachment]:
        """
        Read bill line item attachments for multiple bill line items in a single query.
        Returns list of attachments for the given bill line item public IDs.
        """
        if not bill_line_item_public_ids:
            return []
        return self.repo.read_by_bill_line_item_public_ids(bill_line_item_public_ids)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[BillLineItemAttachment]:
        """
        Delete a bill line item attachment by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing and existing.id:
            return self.repo.delete_by_id(existing.id)
        return None
