# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from entities.invoice_line_item.business.model import InvoiceLineItem
from entities.invoice_line_item.persistence.repo import InvoiceLineItemRepository
from entities.invoice.persistence.repo import InvoiceRepository
from shared.access import assert_can_access_project
from shared.authz import current_user_id

logger = logging.getLogger(__name__)


VALID_SOURCE_TYPES = {"BillLineItem", "ExpenseLineItem", "BillCreditLineItem", "ExpenseRefundLineItem", "Manual"}


def _clear_legacy_invoice_line_item_invoice_line_mapping(invoice_line_item_id: int) -> None:
    """U-362 deploy-gap bridge for InvoiceLineItemService.delete_by_public_id —
    see its call site. Raw SQL, not a repo/model (both retired in this unit):
    deletes any row in the (soon-to-be-dropped) qbo.InvoiceLineItemInvoiceLine
    table that still points at this InvoiceLineItem, so its NO ACTION FK
    (FK_InvoiceLineItemInvoiceLine_InvoiceLineItem, live since
    scripts/migrations/u225_qbo_mapping_fk_gaps.sql) never blocks the line
    delete below. Mirrors BillCreditService's U-353
    _clear_legacy_vendorcredit_billcredit_mapping bridge exactly (same
    OBJECT_ID-guard idiom — table-already-dropped becomes a plain SQL no-op,
    not a caught Python exception on driver error text). Once /em applies the
    DROP for this table, this whole function becomes a permanent no-op and
    should be deleted (see U-365, which did exactly that for the 4 header
    mapping tables)."""
    from shared.database import get_connection

    try:
        with get_connection() as conn:
            conn.cursor().execute(
                "IF OBJECT_ID('qbo.InvoiceLineItemInvoiceLine', 'U') IS NOT NULL "
                "DELETE FROM [qbo].[InvoiceLineItemInvoiceLine] WHERE [InvoiceLineItemId] = ?",
                (invoice_line_item_id,),
            )
    except Exception as e:
        # Only reachable now for a genuine unexpected failure (connection reset,
        # deadlock, permissions) — table-missing no longer raises at all. Logged,
        # not raised: best-effort only. The real safety net is the FK itself — if
        # a mapping row really does still exist and this failed to clear it, the
        # line delete below 547s anyway (fail-safe, not fail-silent-corruption).
        logger.warning(
            f"Could not clear legacy qbo.InvoiceLineItemInvoiceLine mapping for "
            f"InvoiceLineItem {invoice_line_item_id}: {e}"
        )


def _signed_for_billcredit(value: Optional[Decimal]) -> Optional[Decimal]:
    """A BillCredit reduces the draw; every write path stores Price/Amount
    signed-negative (U-344). -abs(), not -value, so it's idempotent over an
    already-negative input."""
    return -abs(value) if value is not None else value


def _assert_can_access_invoice(invoice_id: Optional[int]) -> None:
    """Gate by the parent Invoice's project_id. Loads via repo to avoid recursive
    access checks through InvoiceService."""
    if invoice_id is None:
        return
    invoice = InvoiceRepository().read_by_id(invoice_id)
    if invoice is None:
        return
    assert_can_access_project(invoice.project_id)


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

        if source_type == "BillCreditLineItem":
            price = _signed_for_billcredit(price)
            amount = _signed_for_billcredit(amount)

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
            created_by_user_id=current_user_id.get(),
        )

    def read_all(self) -> list[InvoiceLineItem]:
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[InvoiceLineItem]:
        line_item = self.repo.read_by_id(id)
        if line_item is None:
            return None
        _assert_can_access_invoice(line_item.invoice_id)
        return line_item

    def read_by_public_id(self, public_id: str) -> Optional[InvoiceLineItem]:
        line_item = self.repo.read_by_public_id(public_id)
        if line_item is None:
            return None
        _assert_can_access_invoice(line_item.invoice_id)
        return line_item

    def read_by_invoice_id(self, invoice_id: int) -> list[InvoiceLineItem]:
        _assert_can_access_invoice(invoice_id)
        return self.repo.read_by_invoice_id(invoice_id=invoice_id)

    def read_by_qbo_identity(self, invoice_id: int, qbo_id: str) -> Optional[InvoiceLineItem]:
        """
        Read an invoice line item directly by its dbo-native QBO identity,
        scoped to its parent Invoice (U-293b) — the line-level Phase-4 repoint
        seam, bypassing the qbo.InvoiceLine/qbo.InvoiceLineItemInvoiceLine
        staging/mapping tables.
        """
        _assert_can_access_invoice(invoice_id)
        return self.repo.read_by_qbo_identity(invoice_id=invoice_id, qbo_id=qbo_id)

    def read_by_linked_txn(
        self, invoice_id: int, linked_txn_type: str, linked_txn_id: str
    ) -> Optional[InvoiceLineItem]:
        """
        Read an invoice line item by its U-272 source-provenance linkage,
        scoped to its parent Invoice — U-362b's dbo-native source-linked-line
        recognition seam (see the repo method's own docstring).
        """
        _assert_can_access_invoice(invoice_id)
        return self.repo.read_by_linked_txn(
            invoice_id=invoice_id, linked_txn_type=linked_txn_type, linked_txn_id=linked_txn_id,
        )

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

        # Self-heals ANY caller that writes a raw (possibly positive) value
        # alongside source_type "BillCreditLineItem" — including a routine
        # QBO re-sync's amount write — matching create()'s invariant.
        if existing.source_type == "BillCreditLineItem":
            existing.price = _signed_for_billcredit(existing.price)
            existing.amount = _signed_for_billcredit(existing.amount)

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

        # U-362: qbo.InvoiceLineItemInvoiceLine's CONNECTOR (mapping_repo,
        # create_mapping, the U-293b fastpath's mapping fallback) is retired —
        # dbo.InvoiceLineItem.QboId/RealmId (U-238b) is the sole identity store
        # going forward. The TABLE itself is not dropped by this unit (that's a
        # separate /em-run post-deploy step), and it carries a live NO ACTION FK
        # onto this one (FK_InvoiceLineItemInvoiceLine_InvoiceLineItem, added by
        # scripts/migrations/u225_qbo_mapping_fk_gaps.sql) — so a still-mapped
        # row's delete WOULD 547 without this bridge. Correcting U-241's original
        # comment here: that FK did not exist when this delete path was first
        # written, but it does now.
        _clear_legacy_invoice_line_item_invoice_line_mapping(existing.id)
        return self.repo.delete_by_id(existing.id)
