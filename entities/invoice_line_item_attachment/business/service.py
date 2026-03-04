# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.invoice_line_item_attachment.business.model import InvoiceLineItemAttachment
from entities.invoice_line_item_attachment.persistence.repo import InvoiceLineItemAttachmentRepository
from entities.invoice_line_item.business.service import InvoiceLineItemService
from entities.attachment.business.service import AttachmentService


class InvoiceLineItemAttachmentService:

    def __init__(self, repo: Optional[InvoiceLineItemAttachmentRepository] = None):
        self.repo = repo or InvoiceLineItemAttachmentRepository()

    def create(self, *, tenant_id: int = None, invoice_line_item_public_id: str, attachment_public_id: str) -> InvoiceLineItemAttachment:
        invoice_line_item = InvoiceLineItemService().read_by_public_id(public_id=invoice_line_item_public_id)
        attachment = AttachmentService().read_by_public_id(public_id=attachment_public_id)

        if not invoice_line_item or not invoice_line_item.id:
            raise ValueError(f"InvoiceLineItem with public_id '{invoice_line_item_public_id}' not found")
        if not attachment or not attachment.id:
            raise ValueError(f"Attachment with public_id '{attachment_public_id}' not found")

        invoice_line_item_id = int(invoice_line_item.id)
        attachment_id = int(attachment.id)

        return self.repo.create(invoice_line_item_id=invoice_line_item_id, attachment_id=attachment_id)

    def read_all(self) -> list[InvoiceLineItemAttachment]:
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[InvoiceLineItemAttachment]:
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[InvoiceLineItemAttachment]:
        return self.repo.read_by_public_id(public_id)

    def read_by_invoice_line_item_id(self, invoice_line_item_public_id: str) -> list[InvoiceLineItemAttachment]:
        invoice_line_item = InvoiceLineItemService().read_by_public_id(public_id=invoice_line_item_public_id)
        if not invoice_line_item or not invoice_line_item.id:
            return []
        return self.repo.read_by_invoice_line_item_id(invoice_line_item_id=int(invoice_line_item.id))

    def read_by_invoice_line_item_ids(self, invoice_line_item_public_ids: list[str]) -> list[InvoiceLineItemAttachment]:
        if not invoice_line_item_public_ids:
            return []
        return self.repo.read_by_invoice_line_item_public_ids(invoice_line_item_public_ids)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[InvoiceLineItemAttachment]:
        existing = self.read_by_public_id(public_id=public_id)
        if existing and existing.id:
            return self.repo.delete_by_id(existing.id)
        return None
