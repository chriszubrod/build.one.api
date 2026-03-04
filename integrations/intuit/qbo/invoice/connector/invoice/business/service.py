# Python Standard Library Imports
import logging
from typing import List, Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.invoice.connector.invoice.business.model import InvoiceInvoice
from integrations.intuit.qbo.invoice.connector.invoice.persistence.repo import InvoiceInvoiceRepository
from integrations.intuit.qbo.invoice.connector.invoice_line_item.persistence.repo import InvoiceLineItemInvoiceLineRepository
from integrations.intuit.qbo.invoice.business.model import QboInvoice, QboInvoiceLine
from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository
from integrations.intuit.qbo.customer.connector.project.persistence.repo import CustomerProjectRepository
from entities.invoice.business.service import InvoiceService
from entities.invoice.business.model import Invoice
from entities.project.business.service import ProjectService

logger = logging.getLogger(__name__)


class InvoiceInvoiceConnector:
    """
    Connector service for synchronization between QboInvoice and Invoice modules.
    """

    def __init__(
        self,
        mapping_repo: Optional[InvoiceInvoiceRepository] = None,
        line_mapping_repo: Optional[InvoiceLineItemInvoiceLineRepository] = None,
        invoice_service: Optional[InvoiceService] = None,
        project_service: Optional[ProjectService] = None,
        qbo_customer_repo: Optional[QboCustomerRepository] = None,
        customer_project_repo: Optional[CustomerProjectRepository] = None,
    ):
        """Initialize the InvoiceInvoiceConnector."""
        self.mapping_repo = mapping_repo or InvoiceInvoiceRepository()
        self.line_mapping_repo = line_mapping_repo or InvoiceLineItemInvoiceLineRepository()
        self.invoice_service = invoice_service or InvoiceService()
        self.project_service = project_service or ProjectService()
        self.qbo_customer_repo = qbo_customer_repo or QboCustomerRepository()
        self.customer_project_repo = customer_project_repo or CustomerProjectRepository()

        # In-memory caches to avoid repeated DB lookups across invoice syncs
        self._project_cache: dict = {}          # {qbo_customer_ref_value: project_public_id}
        self._invoice_mapping_cache: dict = {}  # {qbo_invoice_id: InvoiceInvoice}
        self._line_mapping_cache: dict = {}     # {qbo_invoice_line_id: InvoiceLineItemInvoiceLine}
        self._invoice_cache: dict = {}          # {invoice_id: Invoice}
        self._line_item_cache: dict = {}        # {invoice_line_item_id: InvoiceLineItem}

    def preload_caches(self) -> None:
        """
        Pre-load all mapping and module record caches from the database.
        Call once before processing a large batch to eliminate per-invoice DB lookups.
        """
        from entities.invoice_line_item.business.service import InvoiceLineItemService

        logger.info("Pre-loading InvoiceInvoice mapping cache...")
        all_mappings = self.mapping_repo.read_all()
        self._invoice_mapping_cache = {m.qbo_invoice_id: m for m in all_mappings}
        logger.info(f"Pre-loaded {len(self._invoice_mapping_cache)} InvoiceInvoice mappings")

        logger.info("Pre-loading InvoiceLineItemInvoiceLine mapping cache...")
        all_line_mappings = self.line_mapping_repo.read_all()
        self._line_mapping_cache = {m.qbo_invoice_line_id: m for m in all_line_mappings}
        logger.info(f"Pre-loaded {len(self._line_mapping_cache)} InvoiceLineItemInvoiceLine mappings")

        logger.info("Pre-loading Invoice module records...")
        all_invoices = self.invoice_service.read_all()
        self._invoice_cache = {inv.id: inv for inv in all_invoices}
        logger.info(f"Pre-loaded {len(self._invoice_cache)} Invoice records")

        logger.info("Pre-loading InvoiceLineItem module records...")
        line_item_service = InvoiceLineItemService()
        all_line_items = line_item_service.read_all()
        self._line_item_cache = {item.id: item for item in all_line_items}
        logger.info(f"Pre-loaded {len(self._line_item_cache)} InvoiceLineItem records")

    def sync_from_qbo_invoice(self, qbo_invoice: QboInvoice, qbo_invoice_lines: List[QboInvoiceLine]) -> Invoice:
        """
        Sync data from QboInvoice to Invoice module.
        
        This method:
        1. Checks if a mapping exists
        2. Creates or updates the Invoice accordingly
        3. Syncs line items to InvoiceLineItem module
        
        Args:
            qbo_invoice: QboInvoice record
            qbo_invoice_lines: List of QboInvoiceLine records for this invoice
        
        Returns:
            Invoice: The synced Invoice record
        """
        # Find project mapping from QBO CustomerRef
        project_public_id = self._get_project_public_id(qbo_invoice.customer_ref_value)
        if not project_public_id:
            raise ValueError(f"No project mapping found for QBO customer ref: {qbo_invoice.customer_ref_value}")
        
        # Map QBO Invoice fields to Invoice module fields
        invoice_number = qbo_invoice.doc_number or f"QBO-{qbo_invoice.qbo_id}"
        invoice_date = qbo_invoice.txn_date or ""
        due_date = qbo_invoice.due_date or ""
        memo = qbo_invoice.private_note
        total_amount = qbo_invoice.total_amt
        
        # Check for existing mapping (use cache if populated, else fall back to DB)
        if self._invoice_mapping_cache:
            mapping = self._invoice_mapping_cache.get(qbo_invoice.id)
        else:
            mapping = self.mapping_repo.read_by_qbo_invoice_id(qbo_invoice.id)
        
        if mapping:
            # Found existing mapping - use cached Invoice record to avoid DB read
            invoice = self._invoice_cache.get(mapping.invoice_id) if self._invoice_cache else self.invoice_service.read_by_id(mapping.invoice_id)
            if invoice:
                logger.info(f"Updating existing Invoice {invoice.id} from QboInvoice {qbo_invoice.id}")

                invoice = self.invoice_service.update_by_public_id(
                    invoice.public_id,
                    row_version=invoice.row_version,
                    project_public_id=project_public_id,
                    invoice_date=invoice_date,
                    due_date=due_date,
                    invoice_number=invoice_number,
                    total_amount=float(total_amount) if total_amount is not None else None,
                    memo=memo,
                    is_draft=False,
                )
                # Update invoice cache with fresh record
                if self._invoice_cache is not None:
                    self._invoice_cache[invoice.id] = invoice

                # Sync line items for existing invoice
                self._sync_line_items(invoice.id, invoice.public_id, qbo_invoice_lines)

                return invoice
            else:
                logger.warning(f"Mapping exists but Invoice {mapping.invoice_id} not found. Creating new Invoice.")
                self.mapping_repo.delete_by_id(mapping.id)
                mapping = None
        
        # Create new Invoice, handling duplicate invoice numbers
        logger.info(f"Creating new Invoice from QboInvoice {qbo_invoice.id}: invoice_number={invoice_number}")
        create_number = invoice_number
        for attempt in range(10):
            try:
                invoice = self.invoice_service.create(
                    project_public_id=project_public_id,
                    invoice_date=invoice_date,
                    due_date=due_date,
                    invoice_number=create_number,
                    total_amount=total_amount,
                    memo=memo,
                    is_draft=False,
                )
                break
            except ValueError as e:
                if "already exists" in str(e):
                    create_number = f"{invoice_number}-{attempt + 2}"
                    logger.info(f"Duplicate invoice number, retrying with: {create_number}")
                else:
                    raise
        
        # Update invoice cache with newly created record
        if self._invoice_cache is not None:
            self._invoice_cache[invoice.id] = invoice

        # Create mapping
        invoice_id = int(invoice.id) if isinstance(invoice.id, str) else invoice.id
        try:
            mapping = self.create_mapping(invoice_id=invoice_id, qbo_invoice_id=qbo_invoice.id)
            logger.info(f"Created mapping: Invoice {invoice_id} <-> QboInvoice {qbo_invoice.id}")
        except ValueError as e:
            logger.warning(f"Could not create mapping: {e}")

        # Sync line items for new invoice
        self._sync_line_items(invoice_id, invoice.public_id, qbo_invoice_lines)
        
        return invoice

    def _get_project_public_id(self, qbo_customer_ref_value: str) -> Optional[str]:
        """
        Get the Project public_id from QBO customer reference value.
        Results are cached to avoid repeated DB lookups for the same customer.

        Args:
            qbo_customer_ref_value: QBO customer reference value (QBO Customer ID)

        Returns:
            str: Project public_id or None
        """
        if not qbo_customer_ref_value:
            return None

        # Return cached result if available (including None for known misses)
        if qbo_customer_ref_value in self._project_cache:
            return self._project_cache[qbo_customer_ref_value]

        # Find the QboCustomer by qbo_id
        qbo_customer = self.qbo_customer_repo.read_by_qbo_id(qbo_customer_ref_value)
        if not qbo_customer:
            logger.warning(f"QboCustomer not found for qbo_id: {qbo_customer_ref_value}")
            self._project_cache[qbo_customer_ref_value] = None
            return None

        # Find the CustomerProject mapping
        customer_mapping = self.customer_project_repo.read_by_qbo_customer_id(qbo_customer.id)
        if not customer_mapping:
            logger.warning(f"CustomerProject mapping not found for QboCustomer ID: {qbo_customer.id}")
            self._project_cache[qbo_customer_ref_value] = None
            return None

        # Get the Project
        project = self.project_service.read_by_id(customer_mapping.project_id)
        if not project:
            logger.warning(f"Project not found for ID: {customer_mapping.project_id}")
            self._project_cache[qbo_customer_ref_value] = None
            return None

        self._project_cache[qbo_customer_ref_value] = project.public_id
        return project.public_id

    def _sync_line_items(self, invoice_id: int, invoice_public_id: str, qbo_invoice_lines: List[QboInvoiceLine]) -> None:
        """
        Sync invoice line items to InvoiceLineItem module.

        Args:
            invoice_id: Database ID of the Invoice
            invoice_public_id: Public ID of the Invoice
            qbo_invoice_lines: List of QboInvoiceLine records
        """
        if not qbo_invoice_lines:
            return

        # Import here to avoid circular dependencies
        from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service import InvoiceLineItemConnector

        line_connector = InvoiceLineItemConnector(
            line_mapping_cache=self._line_mapping_cache,
            line_item_cache=self._line_item_cache,
        )

        for qbo_line in qbo_invoice_lines:
            try:
                line_connector.sync_from_qbo_invoice_line(invoice_id, invoice_public_id, qbo_line)
            except Exception as e:
                logger.error(f"Failed to sync QboInvoiceLine {qbo_line.id} to InvoiceLineItem: {e}")

    def create_mapping(self, invoice_id: int, qbo_invoice_id: int) -> InvoiceInvoice:
        """
        Create a mapping between Invoice and QboInvoice.
        
        Args:
            invoice_id: Database ID of Invoice record
            qbo_invoice_id: Database ID of QboInvoice record
        
        Returns:
            InvoiceInvoice: The created mapping record
        
        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints only when the cache isn't populated.
        # When the cache is pre-loaded, we already checked it before calling create_mapping.
        if not self._invoice_mapping_cache:
            existing_by_invoice = self.mapping_repo.read_by_invoice_id(invoice_id)
            if existing_by_invoice:
                raise ValueError(
                    f"Invoice {invoice_id} is already mapped to QboInvoice {existing_by_invoice.qbo_invoice_id}"
                )
            existing_by_qbo_invoice = self.mapping_repo.read_by_qbo_invoice_id(qbo_invoice_id)
            if existing_by_qbo_invoice:
                raise ValueError(
                    f"QboInvoice {qbo_invoice_id} is already mapped to Invoice {existing_by_qbo_invoice.invoice_id}"
                )

        # Create mapping and update cache
        new_mapping = self.mapping_repo.create(invoice_id=invoice_id, qbo_invoice_id=qbo_invoice_id)
        self._invoice_mapping_cache[qbo_invoice_id] = new_mapping
        return new_mapping

    def get_mapping_by_invoice_id(self, invoice_id: int) -> Optional[InvoiceInvoice]:
        """
        Get mapping by Invoice ID.
        """
        return self.mapping_repo.read_by_invoice_id(invoice_id)

    def get_mapping_by_qbo_invoice_id(self, qbo_invoice_id: int) -> Optional[InvoiceInvoice]:
        """
        Get mapping by QboInvoice ID.
        """
        return self.mapping_repo.read_by_qbo_invoice_id(qbo_invoice_id)
