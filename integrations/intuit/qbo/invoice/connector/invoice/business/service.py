# Python Standard Library Imports
import logging
from decimal import Decimal
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
                    total_amount=Decimal(str(total_amount)) if total_amount is not None else None,
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

    # ------------------------------------------------------------------
    # Local → QBO direction
    # ------------------------------------------------------------------

    def sync_to_qbo_invoice(self, invoice: Invoice, realm_id: str):
        """
        Push a local Invoice to QuickBooks Online.

        Creates the invoice in QBO if not yet synced. If already mapped,
        returns the existing local QboInvoice mirror without making an API call.

        Args:
            invoice: Local Invoice record
            realm_id: QBO realm ID

        Returns:
            QboInvoice: The local QboInvoice mirror record

        Raises:
            ValueError: If CustomerRef cannot be resolved or no valid lines exist
        """
        from integrations.intuit.qbo.invoice.persistence.repo import QboInvoiceRepository, QboInvoiceLineRepository
        from integrations.intuit.qbo.invoice.external.client import QboInvoiceClient
        from integrations.intuit.qbo.invoice.external.schemas import (
            QboInvoiceCreate as QboInvoiceCreateSchema,
            QboReferenceType,
        )
        from integrations.intuit.qbo.auth.business.service import QboAuthService
        from entities.invoice_line_item.business.service import InvoiceLineItemService

        qbo_invoice_repo = QboInvoiceRepository()
        qbo_invoice_line_repo = QboInvoiceLineRepository()

        invoice_id = int(invoice.id) if isinstance(invoice.id, str) else invoice.id

        # Return early if already pushed to QBO
        existing_mapping = self.mapping_repo.read_by_invoice_id(invoice_id)
        if existing_mapping:
            logger.info(f"Invoice {invoice_id} is already mapped to QboInvoice {existing_mapping.qbo_invoice_id}")
            return qbo_invoice_repo.read_by_id(existing_mapping.qbo_invoice_id)

        # Resolve QBO CustomerRef from project_id
        customer_ref = self._get_qbo_customer_ref(invoice.project_id)
        if not customer_ref:
            raise ValueError(f"No QBO customer mapping found for project_id: {invoice.project_id}")

        # Get invoice line items
        invoice_line_items = InvoiceLineItemService().read_by_invoice_id(invoice_id)

        # Build QBO line items
        qbo_lines = []
        skipped_lines = []
        for line_item in invoice_line_items:
            qbo_line = self._build_qbo_invoice_line(line_item)
            if qbo_line:
                qbo_lines.append(qbo_line)
            else:
                skipped_lines.append(line_item.id)

        if not qbo_lines:
            if invoice_line_items:
                raise ValueError(
                    f"Invoice {invoice_id} has {len(invoice_line_items)} line item(s) but none could be "
                    f"built for QBO. Manual lines require a SubCostCode mapped to a QBO Item. "
                    f"Skipped line item IDs: {skipped_lines}"
                )
            raise ValueError("Invoice has no line items. QBO requires at least one line item.")

        # Build create payload
        qbo_invoice_create = QboInvoiceCreateSchema(
            customer_ref=QboReferenceType(value=customer_ref.value, name=customer_ref.name),
            txn_date=invoice.invoice_date[:10] if invoice.invoice_date else None,
            due_date=invoice.due_date[:10] if invoice.due_date else None,
            doc_number=invoice.invoice_number,
            private_note=invoice.memo,
            line=qbo_lines,
        )

        # Get auth token
        qbo_auth = QboAuthService().ensure_valid_token(realm_id=realm_id)
        if not qbo_auth or not qbo_auth.access_token:
            raise ValueError(f"No valid QBO auth found for realm {realm_id}")

        logger.info(f"Creating Invoice in QBO for local Invoice {invoice_id}: doc_number={invoice.invoice_number}")

        with QboInvoiceClient(access_token=qbo_auth.access_token, realm_id=realm_id) as client:
            created_invoice = client.create_invoice(qbo_invoice_create)

        logger.info(f"Created QBO Invoice {created_invoice.id} (SyncToken={created_invoice.sync_token})")

        # Store local QboInvoice mirror
        local_qbo_invoice = qbo_invoice_repo.create(
            qbo_id=created_invoice.id,
            sync_token=created_invoice.sync_token,
            realm_id=realm_id,
            customer_ref_value=customer_ref.value,
            customer_ref_name=customer_ref.name,
            txn_date=created_invoice.txn_date,
            due_date=created_invoice.due_date,
            ship_date=None,
            doc_number=created_invoice.doc_number,
            private_note=created_invoice.private_note,
            customer_memo=None,
            bill_email=None,
            total_amt=created_invoice.total_amt,
            balance=created_invoice.balance,
            deposit=None,
            sales_term_ref_value=None,
            sales_term_ref_name=None,
            currency_ref_value=created_invoice.currency_ref.value if created_invoice.currency_ref else None,
            currency_ref_name=created_invoice.currency_ref.name if created_invoice.currency_ref else None,
            exchange_rate=created_invoice.exchange_rate,
            department_ref_value=None,
            department_ref_name=None,
            class_ref_value=None,
            class_ref_name=None,
            ship_method_ref_value=None,
            ship_method_ref_name=None,
            tracking_num=None,
            print_status=None,
            email_status=None,
            allow_online_ach_payment=None,
            allow_online_credit_card_payment=None,
            apply_tax_after_discount=None,
            global_tax_calculation=None,
        )

        logger.info(f"Stored local QboInvoice {local_qbo_invoice.id}")

        # Store local QboInvoiceLine mirrors
        if created_invoice.line:
            for qbo_line in created_invoice.line:
                if qbo_line.detail_type != "SalesItemLine":
                    continue
                try:
                    detail = qbo_line.sales_item_line_detail
                    qbo_invoice_line_repo.create(
                        qbo_invoice_id=local_qbo_invoice.id,
                        qbo_line_id=qbo_line.id,
                        line_num=qbo_line.line_num,
                        description=qbo_line.description,
                        amount=qbo_line.amount,
                        detail_type=qbo_line.detail_type,
                        item_ref_value=detail.item_ref.value if detail and detail.item_ref else None,
                        item_ref_name=detail.item_ref.name if detail and detail.item_ref else None,
                        class_ref_value=detail.class_ref.value if detail and detail.class_ref else None,
                        class_ref_name=detail.class_ref.name if detail and detail.class_ref else None,
                        qty=detail.qty if detail else None,
                        unit_price=detail.unit_price if detail else None,
                        tax_code_ref_value=detail.tax_code_ref.value if detail and detail.tax_code_ref else None,
                        tax_code_ref_name=detail.tax_code_ref.name if detail and detail.tax_code_ref else None,
                        service_date=detail.service_date if detail else None,
                        discount_rate=None,
                        discount_amt=None,
                    )
                except Exception as e:
                    logger.warning(f"Could not store QboInvoiceLine for QBO line {qbo_line.id}: {e}")

        # Create InvoiceInvoice mapping
        qbo_invoice_id_local = int(local_qbo_invoice.id) if isinstance(local_qbo_invoice.id, str) else local_qbo_invoice.id
        try:
            self.create_mapping(invoice_id=invoice_id, qbo_invoice_id=qbo_invoice_id_local)
            logger.info(f"Created mapping: Invoice {invoice_id} <-> QboInvoice {qbo_invoice_id_local}")
        except ValueError as e:
            logger.warning(f"Could not create InvoiceInvoice mapping: {e}")

        return local_qbo_invoice

    def _get_qbo_customer_ref(self, project_id: int):
        """
        Resolve local project_id to a QBO CustomerRef (value=qbo_id, name=display_name).

        Returns None if the mapping chain cannot be resolved.
        """
        from integrations.intuit.qbo.invoice.external.schemas import QboReferenceType

        if not project_id:
            return None

        customer_mapping = self.customer_project_repo.read_by_project_id(project_id)
        if not customer_mapping:
            logger.warning(f"CustomerProject mapping not found for project_id: {project_id}")
            return None

        qbo_customer = self.qbo_customer_repo.read_by_id(customer_mapping.qbo_customer_id)
        if not qbo_customer or not qbo_customer.qbo_id:
            logger.warning(f"QboCustomer not found for qbo_customer_id: {customer_mapping.qbo_customer_id}")
            return None

        from integrations.intuit.qbo.invoice.external.schemas import QboReferenceType
        return QboReferenceType(value=qbo_customer.qbo_id, name=qbo_customer.display_name)

    def _build_qbo_invoice_line(self, line_item):
        """
        Build a QBO SalesItemLine from a local InvoiceLineItem.

        Returns None if the line cannot be resolved (no amount, no ItemRef).
        """
        from integrations.intuit.qbo.invoice.external.schemas import (
            QboInvoiceLine as QboInvoiceLineSchema,
            QboSalesItemLineDetail,
            QboReferenceType,
        )

        # Amount charged on the invoice: prefer price (billable), fall back to cost amount
        amount = line_item.price if line_item.price is not None else line_item.amount
        if amount is None:
            logger.warning(f"InvoiceLineItem {line_item.id} has no price or amount, skipping")
            return None

        # Resolve ItemRef — required by QBO for SalesItemLine
        item_ref = self._get_qbo_item_ref_for_line(line_item)
        if not item_ref:
            logger.warning(
                f"InvoiceLineItem {line_item.id} (source_type={line_item.source_type}) "
                f"has no QBO Item mapping, skipping"
            )
            return None

        # Qty / UnitPrice only sent for Manual lines
        qty = line_item.quantity if line_item.source_type == "Manual" else None
        unit_price = line_item.rate if line_item.source_type == "Manual" else None

        detail = QboSalesItemLineDetail(
            item_ref=item_ref,
            qty=qty,
            unit_price=unit_price,
        )

        # LinkedTxn for source-backed lines
        linked_txn = self._resolve_linked_txn_for_line(line_item)

        return QboInvoiceLineSchema(
            description=line_item.description,
            amount=amount,
            detail_type="SalesItemLine",
            sales_item_line_detail=detail,
            linked_txn=[linked_txn] if linked_txn else None,
        )

    def _get_qbo_item_ref_for_line(self, line_item):
        """
        Resolve the QBO ItemRef for a line item by walking:
          Manual           → InvoiceLineItem.sub_cost_code_id
          BillLineItem     → BillLineItem.sub_cost_code_id
          ExpenseLineItem  → ExpenseLineItem.sub_cost_code_id
          BillCreditLineItem → BillCreditLineItem.sub_cost_code_id

        Then: sub_cost_code_id → ItemSubCostCode → QboItem.qbo_id
        """
        from integrations.intuit.qbo.item.connector.sub_cost_code.persistence.repo import ItemSubCostCodeRepository
        from integrations.intuit.qbo.item.persistence.repo import QboItemRepository
        from integrations.intuit.qbo.invoice.external.schemas import QboReferenceType

        sub_cost_code_id = None

        if line_item.source_type == "Manual":
            sub_cost_code_id = line_item.sub_cost_code_id

        elif line_item.source_type == "BillLineItem" and line_item.bill_line_item_id:
            from entities.bill_line_item.business.service import BillLineItemService
            bill_li = BillLineItemService().read_by_id(line_item.bill_line_item_id)
            sub_cost_code_id = bill_li.sub_cost_code_id if bill_li else None

        elif line_item.source_type == "ExpenseLineItem" and line_item.expense_line_item_id:
            from entities.expense_line_item.business.service import ExpenseLineItemService
            expense_li = ExpenseLineItemService().read_by_id(line_item.expense_line_item_id)
            sub_cost_code_id = expense_li.sub_cost_code_id if expense_li else None

        elif line_item.source_type == "BillCreditLineItem" and line_item.bill_credit_line_item_id:
            from entities.bill_credit_line_item.business.service import BillCreditLineItemService
            credit_li = BillCreditLineItemService().read_by_id(line_item.bill_credit_line_item_id)
            sub_cost_code_id = credit_li.sub_cost_code_id if credit_li else None

        if not sub_cost_code_id:
            return None

        mapping = ItemSubCostCodeRepository().read_by_sub_cost_code_id(sub_cost_code_id)
        if not mapping:
            return None

        qbo_item = QboItemRepository().read_by_id(mapping.qbo_item_id)
        if not qbo_item or not qbo_item.qbo_id:
            return None

        return QboReferenceType(value=qbo_item.qbo_id, name=qbo_item.name)

    def _resolve_linked_txn_for_line(self, line_item):
        """
        Resolve the QBO LinkedTxn for a source-backed line item.

        Walk the mapping chain:
          BillLineItem     → BillBill → QboBill.qbo_id       → TxnType "Bill"
          ExpenseLineItem  → PurchaseExpense → QboPurchase.qbo_id → TxnType "Purchase"
          BillCreditLineItem → VendorCreditBillCredit → QboVendorCredit.qbo_id → TxnType "VendorCredit"
          Manual           → None (no linked transaction)

        Returns None if the chain cannot be resolved or for Manual lines.
        """
        from integrations.intuit.qbo.invoice.external.schemas import QboLinkedTxn

        try:
            if line_item.source_type == "BillLineItem" and line_item.bill_line_item_id:
                from entities.bill_line_item.business.service import BillLineItemService
                from integrations.intuit.qbo.bill.connector.bill.persistence.repo import BillBillRepository
                from integrations.intuit.qbo.bill.persistence.repo import QboBillRepository

                bill_li = BillLineItemService().read_by_id(line_item.bill_line_item_id)
                if not bill_li or not bill_li.bill_id:
                    return None

                bill_mapping = BillBillRepository().read_by_bill_id(bill_li.bill_id)
                if not bill_mapping:
                    logger.debug(f"No BillBill mapping for bill_id={bill_li.bill_id}")
                    return None

                qbo_bill = QboBillRepository().read_by_id(bill_mapping.qbo_bill_id)
                if not qbo_bill or not qbo_bill.qbo_id:
                    return None

                return QboLinkedTxn(txn_id=qbo_bill.qbo_id, txn_type="Bill")

            elif line_item.source_type == "ExpenseLineItem" and line_item.expense_line_item_id:
                from entities.expense_line_item.business.service import ExpenseLineItemService
                from integrations.intuit.qbo.purchase.connector.expense.persistence.repo import PurchaseExpenseRepository
                from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseRepository

                expense_li = ExpenseLineItemService().read_by_id(line_item.expense_line_item_id)
                if not expense_li or not expense_li.expense_id:
                    return None

                purchase_mapping = PurchaseExpenseRepository().read_by_expense_id(expense_li.expense_id)
                if not purchase_mapping:
                    logger.debug(f"No PurchaseExpense mapping for expense_id={expense_li.expense_id}")
                    return None

                qbo_purchase = QboPurchaseRepository().read_by_id(purchase_mapping.qbo_purchase_id)
                if not qbo_purchase or not qbo_purchase.qbo_id:
                    return None

                return QboLinkedTxn(txn_id=qbo_purchase.qbo_id, txn_type="Purchase")

            elif line_item.source_type == "BillCreditLineItem" and line_item.bill_credit_line_item_id:
                from entities.bill_credit_line_item.business.service import BillCreditLineItemService
                from integrations.intuit.qbo.vendorcredit.connector.bill_credit.persistence.repo import VendorCreditBillCreditMappingRepository
                from integrations.intuit.qbo.vendorcredit.persistence.repo import QboVendorCreditRepository

                credit_li = BillCreditLineItemService().read_by_id(line_item.bill_credit_line_item_id)
                if not credit_li or not credit_li.bill_credit_id:
                    return None

                vc_mapping = VendorCreditBillCreditMappingRepository().read_by_bill_credit_id(credit_li.bill_credit_id)
                if not vc_mapping:
                    logger.debug(f"No VendorCreditBillCredit mapping for bill_credit_id={credit_li.bill_credit_id}")
                    return None

                qbo_vc = QboVendorCreditRepository().read_by_id(vc_mapping.qbo_vendor_credit_id)
                if not qbo_vc or not qbo_vc.qbo_id:
                    return None

                return QboLinkedTxn(txn_id=qbo_vc.qbo_id, txn_type="VendorCredit")

        except Exception as e:
            logger.warning(f"Error resolving LinkedTxn for InvoiceLineItem {line_item.id}: {e}")

        return None
