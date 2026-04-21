# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.model import InvoiceLineItemInvoiceLine
from integrations.intuit.qbo.invoice.connector.invoice_line_item.persistence.repo import InvoiceLineItemInvoiceLineRepository
from integrations.intuit.qbo.invoice.business.model import QboInvoiceLine
from entities.invoice_line_item.business.service import InvoiceLineItemService
from entities.invoice_line_item.business.model import InvoiceLineItem
from entities.invoice.business.service import InvoiceService

logger = logging.getLogger(__name__)


class InvoiceLineItemConnector:
    """
    Connector service for synchronization between QboInvoiceLine and InvoiceLineItem modules.
    """

    def __init__(
        self,
        mapping_repo: Optional[InvoiceLineItemInvoiceLineRepository] = None,
        invoice_line_item_service: Optional[InvoiceLineItemService] = None,
        invoice_service: Optional[InvoiceService] = None,
        line_mapping_cache: Optional[dict] = None,
        line_item_cache: Optional[dict] = None,
    ):
        """Initialize the InvoiceLineItemConnector."""
        self.mapping_repo = mapping_repo or InvoiceLineItemInvoiceLineRepository()
        self.invoice_line_item_service = invoice_line_item_service or InvoiceLineItemService()
        self.invoice_service = invoice_service or InvoiceService()
        # Shared cache from the parent connector: {qbo_invoice_line_id: InvoiceLineItemInvoiceLine}
        self._line_mapping_cache: dict = line_mapping_cache if line_mapping_cache is not None else {}
        # Shared cache from the parent connector: {invoice_line_item_id: InvoiceLineItem}
        self._line_item_cache: dict = line_item_cache if line_item_cache is not None else {}

    def sync_from_qbo_invoice_line(
        self,
        invoice_id: int,
        invoice_public_id: str,
        qbo_invoice_line: QboInvoiceLine,
    ) -> InvoiceLineItem:
        """
        Sync data from QboInvoiceLine to InvoiceLineItem module.
        
        This method:
        1. Checks if a mapping exists
        2. Creates or updates the InvoiceLineItem accordingly
        
        Args:
            invoice_id: Database ID of the Invoice in our system
            invoice_public_id: Public ID of the Invoice
            qbo_invoice_line: QboInvoiceLine record
        
        Returns:
            InvoiceLineItem: The synced InvoiceLineItem record
        """
        # Map QBO InvoiceLine fields to InvoiceLineItem module fields
        description = qbo_invoice_line.description
        amount = qbo_invoice_line.amount
        
        # Calculate price from line detail. QBO invoices don't have a direct "markup"
        # concept (discount_rate is a reduction, not an addition), so markup is left None.
        # The QBO amount field already reflects all discounts applied.
        markup = None
        price = None
        if qbo_invoice_line.unit_price is not None and qbo_invoice_line.qty is not None:
            price = qbo_invoice_line.unit_price
        
        # Check for existing mapping (use cache if populated, else fall back to DB)
        if self._line_mapping_cache:
            mapping = self._line_mapping_cache.get(qbo_invoice_line.id)
        else:
            mapping = self.mapping_repo.read_by_qbo_invoice_line_id(qbo_invoice_line.id)

        if not mapping:
            # Shape B fallback (task #17): content-fingerprint match when QBO
            # regenerates line IDs. Only applies to Manual-sourced invoice lines;
            # Bill/Expense-sourced lines are matched via their source FKs, not
            # by fingerprint, so we skip them here to avoid double-adoption.
            orphan = self._find_and_match_manual_by_fingerprint(
                invoice_id=invoice_id,
                description=description,
                amount=amount,
            )
            if orphan is not None:
                logger.info(
                    f"Adopting orphaned InvoiceLineItem {orphan.id} for QboInvoiceLine "
                    f"{qbo_invoice_line.id} via content fingerprint match"
                )
                try:
                    mapping = self.create_mapping(
                        invoice_line_item_id=int(orphan.id),
                        qbo_invoice_line_id=qbo_invoice_line.id,
                    )
                except Exception as error:
                    logger.warning(
                        f"Could not adopt orphaned InvoiceLineItem {orphan.id}: {error}"
                    )

        if mapping:
            # Found existing mapping - use cached record to avoid DB read
            line_item = self._line_item_cache.get(mapping.invoice_line_item_id) if self._line_item_cache else self.invoice_line_item_service.read_by_id(mapping.invoice_line_item_id)
            if line_item:
                logger.info(f"Updating existing InvoiceLineItem {line_item.id} from QboInvoiceLine {qbo_invoice_line.id}")

                line_item = self.invoice_line_item_service.update_by_public_id(
                    line_item.public_id,
                    row_version=line_item.row_version,
                    invoice_public_id=invoice_public_id,
                    source_type="Manual",
                    description=description,
                    amount=Decimal(str(amount)) if amount is not None else None,
                    markup=Decimal(str(markup)) if markup is not None else None,
                    price=Decimal(str(price)) if price is not None else None,
                    is_draft=False,
                )
                # Update line item cache with fresh record
                if self._line_item_cache is not None:
                    self._line_item_cache[line_item.id] = line_item
                return line_item
            else:
                logger.warning(f"Mapping exists but InvoiceLineItem {mapping.invoice_line_item_id} not found. Creating new.")
                self.mapping_repo.delete_by_id(mapping.id)
                mapping = None
        
        # Create new InvoiceLineItem
        logger.info(f"Creating new InvoiceLineItem from QboInvoiceLine {qbo_invoice_line.id}")
        line_item = self.invoice_line_item_service.create(
            invoice_public_id=invoice_public_id,
            source_type="Manual",
            description=description,
            amount=amount,
            markup=markup,
            price=price,
            is_draft=False,
        )
        # Add to cache
        if self._line_item_cache is not None:
            self._line_item_cache[line_item.id] = line_item

        # Create mapping
        line_item_id = int(line_item.id) if isinstance(line_item.id, str) else line_item.id
        try:
            mapping = self.create_mapping(invoice_line_item_id=line_item_id, qbo_invoice_line_id=qbo_invoice_line.id)
            logger.info(f"Created mapping: InvoiceLineItem {line_item_id} <-> QboInvoiceLine {qbo_invoice_line.id}")
        except ValueError as e:
            logger.warning(f"Could not create mapping: {e}")
        
        return line_item

    # ------------------------------------------------------------------ #
    # Shape B line-matching helpers (task #17)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize_for_fingerprint(value) -> str:
        """Canonicalize a value for content-fingerprint comparison."""
        if value is None:
            return ""
        if isinstance(value, Decimal):
            return format(value.normalize(), "f")
        try:
            return format(Decimal(str(value)).normalize(), "f")
        except Exception:
            pass
        return str(value).strip()

    def _find_and_match_manual_by_fingerprint(
        self,
        *,
        invoice_id: int,
        description,
        amount,
    ):
        """
        Find at most one unmapped InvoiceLineItem on this invoice whose
        content matches.

        Only considers lines with `source_type='Manual'`. Bill- and
        Expense-sourced invoice lines are matched via their source FKs
        elsewhere; attempting to adopt them here would break the source
        linkage.

        Uses (description, amount) as the fingerprint — qty/rate are less
        reliable for invoice lines because QBO can normalize them during
        entry. Returns None on zero or ambiguous matches.
        """
        from entities.invoice_line_item.business.service import InvoiceLineItemService

        existing = InvoiceLineItemService().read_by_invoice_id(invoice_id)
        unmapped = []
        for li in existing:
            if getattr(li, "source_type", None) != "Manual":
                continue
            if self.mapping_repo.read_by_invoice_line_item_id(int(li.id)):
                continue
            unmapped.append(li)

        target = (
            self._normalize_for_fingerprint(description),
            self._normalize_for_fingerprint(amount),
        )

        matches = []
        for candidate in unmapped:
            candidate_fp = (
                self._normalize_for_fingerprint(getattr(candidate, "description", None)),
                self._normalize_for_fingerprint(getattr(candidate, "amount", None)),
            )
            if candidate_fp == target:
                matches.append(candidate)

        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            logger.info(
                f"Content-fingerprint match ambiguous: {len(matches)} unmapped "
                f"Manual InvoiceLineItems have identical fingerprint; creating new"
            )
        return None

    def create_mapping(self, invoice_line_item_id: int, qbo_invoice_line_id: int) -> InvoiceLineItemInvoiceLine:
        """
        Create a mapping between InvoiceLineItem and QboInvoiceLine.
        
        Args:
            invoice_line_item_id: Database ID of InvoiceLineItem record
            qbo_invoice_line_id: Database ID of QboInvoiceLine record
        
        Returns:
            InvoiceLineItemInvoiceLine: The created mapping record
        
        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints only when the cache isn't populated.
        # When the cache is pre-loaded, we already checked it before calling create_mapping.
        if not self._line_mapping_cache:
            existing_by_line_item = self.mapping_repo.read_by_invoice_line_item_id(invoice_line_item_id)
            if existing_by_line_item:
                raise ValueError(
                    f"InvoiceLineItem {invoice_line_item_id} is already mapped to QboInvoiceLine {existing_by_line_item.qbo_invoice_line_id}"
                )
            existing_by_qbo_line = self.mapping_repo.read_by_qbo_invoice_line_id(qbo_invoice_line_id)
            if existing_by_qbo_line:
                raise ValueError(
                    f"QboInvoiceLine {qbo_invoice_line_id} is already mapped to InvoiceLineItem {existing_by_qbo_line.invoice_line_item_id}"
                )

        # Create mapping and update cache
        new_mapping = self.mapping_repo.create(invoice_line_item_id=invoice_line_item_id, qbo_invoice_line_id=qbo_invoice_line_id)
        self._line_mapping_cache[qbo_invoice_line_id] = new_mapping
        return new_mapping

    def get_mapping_by_invoice_line_item_id(self, invoice_line_item_id: int) -> Optional[InvoiceLineItemInvoiceLine]:
        """
        Get mapping by InvoiceLineItem ID.
        """
        return self.mapping_repo.read_by_invoice_line_item_id(invoice_line_item_id)

    def get_mapping_by_qbo_invoice_line_id(self, qbo_invoice_line_id: int) -> Optional[InvoiceLineItemInvoiceLine]:
        """
        Get mapping by QboInvoiceLine ID.
        """
        return self.mapping_repo.read_by_qbo_invoice_line_id(qbo_invoice_line_id)
