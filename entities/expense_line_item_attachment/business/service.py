# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from entities.expense_line_item_attachment.business.model import ExpenseLineItemAttachment
from entities.expense_line_item_attachment.persistence.repo import ExpenseLineItemAttachmentRepository
from entities.expense_line_item.business.service import ExpenseLineItemService
from entities.attachment.business.service import AttachmentService
from shared.authz import current_user_id, current_is_system_admin
from shared.lifecycle.terminal_lock import is_exempt

logger = logging.getLogger(__name__)


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

        # U-468: attachments are evidence for the AP that already shipped —
        # a completed expense's document set is frozen with it.
        ExpenseLineItemService()._assert_parent_editable(
            expense_id=getattr(expense_line_item, "expense_id", None),
            what="attachments cannot be added to it",
        )

        if not attachment or not attachment.id:
            raise ValueError(f"Attachment with public_id '{attachment_public_id}' not found")
        
        expense_line_item_id = int(expense_line_item.id)
        attachment_id = int(attachment.id)
        
        # Check if an ExpenseLineItemAttachment already exists for this ExpenseLineItem (1-1 relationship)
        existing = self.repo.read_by_expense_line_item_id(expense_line_item_id=expense_line_item_id)
        if existing:
            if existing.attachment_id != attachment_id:
                logger.warning(
                    "ExpenseLineItemAttachment already exists for ELI %s pointing to attachment %s, "
                    "but caller requested attachment %s — returning existing (stale blob risk)",
                    expense_line_item_public_id,
                    existing.attachment_id,
                    attachment_id,
                )
            return existing
        
        # No existing attachment for this ExpenseLineItem - safe to create
        return self.repo.create(
            expense_line_item_id=expense_line_item_id,
            attachment_id=attachment_id,
            created_by_user_id=current_user_id.get(),
            allow_terminal_parent=is_exempt(),
        )

    def read_all(self) -> list[ExpenseLineItemAttachment]:
        """
        Read expense line item attachments, scoped by UserProject for non-admin
        actors. Scoping happens in the sproc, via the parent ExpenseLineItem's
        Expense.
        """
        return self.repo.read_all(
            actor_user_id=current_user_id.get(),
            actor_is_system_admin=current_is_system_admin.get(),
        )

    def read_by_id(self, id: int) -> Optional[ExpenseLineItemAttachment]:
        """
        Read an expense line item attachment by ID, gated on the parent Expense.
        """
        link = self.repo.read_by_id(id)
        return self._gated(link)

    def read_by_public_id(self, public_id: str) -> Optional[ExpenseLineItemAttachment]:
        """
        Read an expense line item attachment by public ID, gated on the parent Expense.
        """
        link = self.repo.read_by_public_id(public_id)
        return self._gated(link)

    def _gated(
        self, link: Optional[ExpenseLineItemAttachment]
    ) -> Optional[ExpenseLineItemAttachment]:
        """Assert the caller may see this link's parent Expense, then return it.

        Single-row reads gate here rather than in the sproc — the same split
        `read_all` (sproc-scoped) vs `read_by_*` (service-gated) that Expense
        and ExpenseLineItem already use. Resolving the line item goes through
        ExpenseLineItemService, which itself asserts on the parent Expense, so
        an inaccessible link raises EntityNotAccessibleError → 404, never 403.
        """
        if link is None or link.expense_line_item_id is None:
            return link
        ExpenseLineItemService().read_by_id(int(link.expense_line_item_id))
        return link

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
        _link = self.repo.read_by_public_id(public_id=public_id)
        _eli = None
        if _link is not None and getattr(_link, "expense_line_item_id", None):
            _eli = ExpenseLineItemService().read_by_id(int(_link.expense_line_item_id))
            if _eli is not None:
                ExpenseLineItemService()._assert_parent_editable(
                    expense_id=getattr(_eli, "expense_id", None),
                    what="its attachments cannot be deleted",
                )

        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing and existing.id:
            deleted = self.repo.delete_by_id(
                existing.id, allow_terminal_parent=is_exempt()
            )
            if deleted is None and _eli is not None:
                ExpenseLineItemService()._assert_parent_editable(
                    expense_id=getattr(_eli, "expense_id", None),
                    what="its attachments cannot be deleted",
                )
            return deleted
        return None
