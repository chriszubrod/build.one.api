# Python Standard Library Imports
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from entities.invoice_line_item.business.model import InvoiceLineItem
from entities.invoice_line_item.persistence.repo import InvoiceLineItemRepository


VALID_SOURCE_TYPES = {"BillLineItem", "ExpenseLineItem", "BillCreditLineItem", "ExpenseRefundLineItem", "Manual"}


class InvoiceLineItemService:
    """
    Service for InvoiceLineItem entity business operations.
    """

    def __init__(self, repo: Optional[InvoiceLineItemRepository] = None):
        self.repo = repo or InvoiceLineItemRepository()

    def create(
        self,
        *,
        tenant_id: int = None,
        invoice_public_id: str,
        source_type: str,
        bill_line_item_id: Optional[int] = None,
        expense_line_item_id: Optional[int] = None,
        bill_credit_line_item_id: Optional[int] = None,
        sub_cost_code_id: Optional[int] = None,
        description: Optional[str] = None,
        quantity: Optional[Decimal] = None,
        rate: Optional[Decimal] = None,
        amount: Optional[Decimal] = None,
        markup: Optional[Decimal] = None,
        price: Optional[Decimal] = None,
        is_draft: bool = True,
    ) -> InvoiceLineItem:
        from entities.invoice.business.service import InvoiceService

        invoice = InvoiceService().read_by_public_id(public_id=invoice_public_id)
        if not invoice:
            raise ValueError(f"Invoice with public_id '{invoice_public_id}' not found.")

        if source_type not in VALID_SOURCE_TYPES:
            raise ValueError(f"Invalid source_type '{source_type}'. Must be one of: {', '.join(VALID_SOURCE_TYPES)}")

        return self.repo.create(
            invoice_id=invoice.id,
            source_type=source_type,
            bill_line_item_id=bill_line_item_id,
            expense_line_item_id=expense_line_item_id,
            bill_credit_line_item_id=bill_credit_line_item_id,
            sub_cost_code_id=sub_cost_code_id,
            description=description,
            quantity=quantity,
            rate=rate,
            amount=amount,
            markup=markup,
            price=price,
            is_draft=is_draft,
        )

    def read_all(self) -> list[InvoiceLineItem]:
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[InvoiceLineItem]:
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[InvoiceLineItem]:
        return self.repo.read_by_public_id(public_id)

    def read_by_invoice_id(self, invoice_id: int) -> list[InvoiceLineItem]:
        return self.repo.read_by_invoice_id(invoice_id=invoice_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        invoice_public_id: str = None,
        source_type: str = None,
        bill_line_item_id: int = None,
        expense_line_item_id: int = None,
        bill_credit_line_item_id: int = None,
        sub_cost_code_id: int = None,
        description: str = None,
        quantity: Decimal = None,
        rate: Decimal = None,
        amount: Decimal = None,
        markup: Decimal = None,
        price: Decimal = None,
        is_draft: bool = None,
    ) -> Optional[InvoiceLineItem]:
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        existing.row_version = row_version

        if invoice_public_id is not None:
            from entities.invoice.business.service import InvoiceService
            invoice = InvoiceService().read_by_public_id(public_id=invoice_public_id)
            if not invoice:
                raise ValueError(f"Invoice with public_id '{invoice_public_id}' not found.")
            existing.invoice_id = invoice.id

        if source_type is not None:
            if source_type not in VALID_SOURCE_TYPES:
                raise ValueError(f"Invalid source_type '{source_type}'.")
            existing.source_type = source_type
        if bill_line_item_id is not None:
            existing.bill_line_item_id = bill_line_item_id
        if expense_line_item_id is not None:
            existing.expense_line_item_id = expense_line_item_id
        if bill_credit_line_item_id is not None:
            existing.bill_credit_line_item_id = bill_credit_line_item_id
        if sub_cost_code_id is not None:
            existing.sub_cost_code_id = sub_cost_code_id
        if description is not None:
            existing.description = description
        if quantity is not None:
            existing.quantity = Decimal(str(quantity))
        if rate is not None:
            existing.rate = Decimal(str(rate))
        if amount is not None:
            existing.amount = Decimal(str(amount))
        if markup is not None:
            existing.markup = Decimal(str(markup))
        if price is not None:
            existing.price = Decimal(str(price))
        if is_draft is not None:
            existing.is_draft = is_draft

        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[InvoiceLineItem]:
        existing = self.read_by_public_id(public_id=public_id)
        if not existing or not existing.id:
            return None

        from entities.invoice_line_item_attachment.business.service import InvoiceLineItemAttachmentService
        from entities.attachment.business.service import AttachmentService
        from shared.storage import AzureBlobStorage
        ilia_service = InvoiceLineItemAttachmentService()
        attachment_service = AttachmentService()

        line_item_attachments = ilia_service.repo.read_by_invoice_line_item_id(
            invoice_line_item_id=existing.id
        )
        for lia in line_item_attachments:
            # Read attachment record before breaking the FK link
            att = None
            try:
                if lia.attachment_id:
                    att = attachment_service.read_by_id(id=lia.attachment_id)
            except Exception:
                pass

            # Delete the join record FIRST (releases FK_InvoiceLineItemAttachment_Attachment
            # and FK_InvoiceLineItemAttachment_InvoiceLineItem constraints)
            try:
                if lia.id:
                    ilia_service.repo.delete_by_id(lia.id)
            except Exception:
                pass

            # Then delete blob and attachment record
            if att:
                try:
                    if att.blob_url:
                        AzureBlobStorage().delete_file(att.blob_url)
                except Exception:
                    pass
                try:
                    attachment_service.delete_by_public_id(public_id=att.public_id)
                except Exception:
                    pass

        return self.repo.delete_by_id(existing.id)
