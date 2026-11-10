# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from entities.invoice_attachment.business.model import InvoiceAttachment
from entities.invoice_attachment.persistence.repo import InvoiceAttachmentRepository

logger = logging.getLogger(__name__)


class InvoiceAttachmentService:
    """
    Service for InvoiceAttachment entity business operations.
    """

    def __init__(self, repo: Optional[InvoiceAttachmentRepository] = None):
        self.repo = repo or InvoiceAttachmentRepository()

    def create(self, *, invoice_id: int, attachment_id: int) -> InvoiceAttachment:
        """Link an attachment to an invoice.

        U-166c fix round 2. Defense in depth: the route is system-admin only, and
        the packet generator is the only internal caller. Both sides resolve
        through their gated reads, so a link can never grant read of an attachment
        the actor could not already open. A denial raises EntityNotAccessibleError
        and nothing is written.
        """
        from entities.invoice.business.service import InvoiceService
        from entities.attachment.business.service import AttachmentService

        if InvoiceService().read_by_id(invoice_id) is None:
            raise ValueError(f"Invoice {invoice_id} not found")
        if AttachmentService().read_by_id(id=attachment_id) is None:
            raise ValueError(f"Attachment {attachment_id} not found")
        return self.repo.create(invoice_id=invoice_id, attachment_id=attachment_id)

    def read_all(self) -> list[InvoiceAttachment]:
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[InvoiceAttachment]:
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[InvoiceAttachment]:
        return self.repo.read_by_public_id(public_id)

    def read_by_invoice_id(self, invoice_id: int) -> list[InvoiceAttachment]:
        return self.repo.read_by_invoice_id(invoice_id=invoice_id)

    def delete_by_id(self, id: int) -> Optional[InvoiceAttachment]:
        return self.repo.delete_by_id(id)

    def delete_by_public_id(self, public_id: str) -> Optional[InvoiceAttachment]:
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
