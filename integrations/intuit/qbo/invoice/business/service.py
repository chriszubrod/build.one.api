# Python Standard Library Imports
import logging
import time
from typing import List, Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.invoice.business.model import QboInvoice, QboInvoiceLine
from integrations.intuit.qbo.invoice.persistence.repo import QboInvoiceRepository, QboInvoiceLineRepository
from integrations.intuit.qbo.invoice.external.client import QboInvoiceClient
from integrations.intuit.qbo.invoice.external.schemas import QboInvoice as QboInvoiceExternalSchema
from shared.database import with_retry

logger = logging.getLogger(__name__)

# Sync configuration
BATCH_SIZE = 10  # Process invoices in batches
BATCH_DELAY = 0.5  # Delay between batches (seconds)
MAX_RETRIES = 3  # Max retries for transient errors
INITIAL_RETRY_DELAY = 2.0  # Initial retry delay (seconds)


class QboInvoiceService:
    """
    Service for QboInvoice entity business operations.
    """

    def __init__(
        self,
        repo: Optional[QboInvoiceRepository] = None,
        line_repo: Optional[QboInvoiceLineRepository] = None,
    ):
        """Initialize the QboInvoiceService."""
        self.repo = repo or QboInvoiceRepository()
        self.line_repo = line_repo or QboInvoiceLineRepository()

    def sync_from_qbo(
        self,
        realm_id: str,
        last_updated_time: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        customer_ref: Optional[str] = None,
        sync_to_modules: bool = False,
    ) -> List[QboInvoice]:
        """
        Fetch Invoices from QBO API and store locally.
        Uses upsert pattern: creates if not exists, updates if exists.
        
        Args:
            realm_id: QBO company realm ID
            last_updated_time: Optional ISO format datetime string. If provided, only fetches
                Invoices where Metadata.LastUpdatedTime > last_updated_time.
            start_date: Optional date string (YYYY-MM-DD). If provided, only fetches
                Invoices where TxnDate >= start_date.
            end_date: Optional date string (YYYY-MM-DD). If provided, only fetches
                Invoices where TxnDate <= end_date.
            customer_ref: Optional QBO Customer ID. If provided, only fetches
                Invoices where CustomerRef = customer_ref.
            sync_to_modules: If True, also sync to Invoice/InvoiceLineItem modules
        
        Returns:
            List[QboInvoice]: The synced invoice records
        """
        # Fetch Invoices from QBO API. QboHttpClient (via QboInvoiceClient) resolves
        # and refreshes the access token lazily, so no upfront auth call is needed.
        with QboInvoiceClient(realm_id=realm_id) as client:
            qbo_invoices: List[QboInvoiceExternalSchema] = client.query_all_invoices(
                last_updated_time=last_updated_time,
                start_date=start_date,
                end_date=end_date,
                customer_ref=customer_ref,
            )
        
        if not qbo_invoices:
            logger.info(f"No Invoices found since {last_updated_time or 'beginning'}")
            return []
        
        logger.info(f"Retrieved {len(qbo_invoices)} invoices from QBO")

        # Pre-load existing invoices and lines into memory to avoid N+1 queries
        logger.info("Pre-loading existing QboInvoices and QboInvoiceLines into memory...")
        existing_invoices = self.repo.read_by_realm_id(realm_id)
        existing_map = {inv.qbo_id: inv for inv in existing_invoices}
        logger.info(f"Pre-loaded {len(existing_map)} existing QboInvoices")

        existing_lines = self.line_repo.read_all()
        existing_lines_map = {}
        for line in existing_lines:
            key = (line.qbo_invoice_id, line.qbo_line_id)
            existing_lines_map[key] = line
        logger.info(f"Pre-loaded {len(existing_lines_map)} existing QboInvoiceLines")

        # Process each invoice with retry logic and batch delays
        synced_invoices = []
        changed_invoices = []  # Only invoices actually modified (not sync_token-skipped)
        failed_invoices = []

        for i, qbo_invoice in enumerate(qbo_invoices):
            try:
                # Capture pre-sync token to detect whether the invoice was skipped
                existing_before = existing_map.get(qbo_invoice.id)
                local_invoice = with_retry(
                    self._upsert_invoice,
                    qbo_invoice,
                    realm_id,
                    existing_map,
                    existing_lines_map,
                    max_retries=MAX_RETRIES,
                    initial_delay=INITIAL_RETRY_DELAY,
                )
                synced_invoices.append(local_invoice)
                # Only propagate to modules if the invoice was actually created or updated
                if existing_before is None or existing_before.sync_token != qbo_invoice.sync_token:
                    changed_invoices.append(local_invoice)
                logger.debug(f"Upserted invoice {qbo_invoice.id} ({i + 1}/{len(qbo_invoices)})")
            except Exception as e:
                logger.error(f"Failed to upsert invoice {qbo_invoice.id}: {e}")
                failed_invoices.append(qbo_invoice.id)

            if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(qbo_invoices):
                logger.debug(f"Processed {i + 1}/{len(qbo_invoices)} invoices, pausing...")
                time.sleep(BATCH_DELAY)
        
        if failed_invoices:
            logger.warning(f"Failed to upsert {len(failed_invoices)} invoices: {failed_invoices}")
        
        # Sync to modules if requested (only changed invoices — skipped ones haven't changed)
        if sync_to_modules:
            self._sync_to_invoices(changed_invoices)
        
        return synced_invoices

    def upsert_from_external(
        self, qbo_invoice: QboInvoiceExternalSchema, realm_id: str,
    ) -> tuple[QboInvoice, List[QboInvoiceLine]]:
        """
        Persist an external-schema QboInvoice (+ its inline lines) into the local
        cache and return the stored dataclass form. See QboBillService.upsert_from_external
        for the rationale — connectors expect the flat dataclass shape.
        """
        local_invoice = self._upsert_invoice(qbo_invoice, realm_id)
        lines = self.line_repo.read_by_qbo_invoice_id(local_invoice.id)
        return local_invoice, lines

    def _upsert_invoice(
        self,
        qbo_invoice: QboInvoiceExternalSchema,
        realm_id: str,
        existing_map: dict = None,
        existing_lines_map: dict = None,
    ) -> QboInvoice:
        """
        Create or update a QboInvoice record along with its line items.

        Args:
            qbo_invoice: QBO Invoice from external API
            realm_id: QBO realm ID
            existing_map: Pre-loaded dict of {qbo_id: QboInvoice} for fast lookup
            existing_lines_map: Pre-loaded dict of {(qbo_invoice_id, qbo_line_id): QboInvoiceLine}

        Returns:
            QboInvoice: The created or updated record
        """
        # Check if invoice already exists (use pre-loaded cache if available)
        if existing_map is not None:
            existing = existing_map.get(qbo_invoice.id)
        else:
            existing = self.repo.read_by_qbo_id_and_realm_id(qbo_id=qbo_invoice.id, realm_id=realm_id)
        
        # Extract reference fields
        customer_ref_value = qbo_invoice.customer_ref.value if qbo_invoice.customer_ref else None
        customer_ref_name = qbo_invoice.customer_ref.name if qbo_invoice.customer_ref else None
        sales_term_ref_value = qbo_invoice.sales_term_ref.value if qbo_invoice.sales_term_ref else None
        sales_term_ref_name = qbo_invoice.sales_term_ref.name if qbo_invoice.sales_term_ref else None
        currency_ref_value = qbo_invoice.currency_ref.value if qbo_invoice.currency_ref else None
        currency_ref_name = qbo_invoice.currency_ref.name if qbo_invoice.currency_ref else None
        department_ref_value = qbo_invoice.department_ref.value if qbo_invoice.department_ref else None
        department_ref_name = qbo_invoice.department_ref.name if qbo_invoice.department_ref else None
        class_ref_value = qbo_invoice.class_ref.value if qbo_invoice.class_ref else None
        class_ref_name = qbo_invoice.class_ref.name if qbo_invoice.class_ref else None
        ship_method_ref_value = qbo_invoice.ship_method_ref.value if qbo_invoice.ship_method_ref else None
        ship_method_ref_name = qbo_invoice.ship_method_ref.name if qbo_invoice.ship_method_ref else None
        customer_memo = qbo_invoice.customer_memo.value if qbo_invoice.customer_memo else None
        bill_email = qbo_invoice.bill_email.address if qbo_invoice.bill_email else None
        
        kwargs = dict(
            sync_token=qbo_invoice.sync_token,
            realm_id=realm_id,
            customer_ref_value=customer_ref_value,
            customer_ref_name=customer_ref_name,
            txn_date=qbo_invoice.txn_date,
            due_date=qbo_invoice.due_date,
            ship_date=qbo_invoice.ship_date,
            doc_number=qbo_invoice.doc_number,
            private_note=qbo_invoice.private_note,
            customer_memo=customer_memo,
            bill_email=bill_email,
            total_amt=qbo_invoice.total_amt,
            balance=qbo_invoice.balance,
            deposit=qbo_invoice.deposit,
            sales_term_ref_value=sales_term_ref_value,
            sales_term_ref_name=sales_term_ref_name,
            currency_ref_value=currency_ref_value,
            currency_ref_name=currency_ref_name,
            exchange_rate=qbo_invoice.exchange_rate,
            department_ref_value=department_ref_value,
            department_ref_name=department_ref_name,
            class_ref_value=class_ref_value,
            class_ref_name=class_ref_name,
            ship_method_ref_value=ship_method_ref_value,
            ship_method_ref_name=ship_method_ref_name,
            tracking_num=qbo_invoice.tracking_num,
            print_status=qbo_invoice.print_status,
            email_status=qbo_invoice.email_status,
            allow_online_ach_payment=qbo_invoice.allow_online_ach_payment,
            allow_online_credit_card_payment=qbo_invoice.allow_online_credit_card_payment,
            apply_tax_after_discount=qbo_invoice.apply_tax_after_discount,
            global_tax_calculation=qbo_invoice.global_tax_calculation,
        )
        
        if existing:
            if existing.sync_token == qbo_invoice.sync_token:
                logger.debug(f"QBO invoice {qbo_invoice.id} sync_token unchanged ({qbo_invoice.sync_token}), skipping update")
                return existing
            logger.debug(f"Updating existing QBO invoice {qbo_invoice.id}")
            local_invoice = self.repo.update_by_qbo_id(
                qbo_id=qbo_invoice.id,
                row_version=existing.row_version_bytes,
                **kwargs,
            )
        else:
            logger.debug(f"Creating new QBO invoice {qbo_invoice.id}")
            local_invoice = self.repo.create(
                qbo_id=qbo_invoice.id,
                **kwargs,
            )
        
        # Update cache with newly created/updated invoice
        if existing_map is not None:
            existing_map[qbo_invoice.id] = local_invoice

        # Upsert line items
        if qbo_invoice.line:
            self._upsert_invoice_lines(local_invoice.id, qbo_invoice.line, existing_lines_map)
        
        return local_invoice

    def _upsert_invoice_lines(self, qbo_invoice_id: int, lines: list, existing_lines_map: dict = None) -> None:
        """
        Upsert invoice line items.

        After inserting/updating all lines present in the QBO API response,
        any locally-stored QboInvoiceLine whose qbo_line_id is NOT in the
        current response is stale (line was removed in QBO). Stale lines are
        deleted along with their InvoiceLineItemInvoiceLine mappings.

        Args:
            qbo_invoice_id: Database ID of the QboInvoice
            lines: List of QboInvoiceLine from external API
            existing_lines_map: Pre-loaded dict of {(qbo_invoice_id, qbo_line_id): QboInvoiceLine}
        """
        # Only store actual detail lines, skip computed summary lines
        SKIP_DETAIL_TYPES = {"SubTotalLineDetail"}

        current_qbo_line_ids = {line.id for line in lines if line.id and line.detail_type not in SKIP_DETAIL_TYPES}

        for line in lines:
            if line.detail_type in SKIP_DETAIL_TYPES:
                continue

            # Extract detail-specific fields based on detail type
            item_ref_value = None
            item_ref_name = None
            class_ref_value = None
            class_ref_name = None
            qty = None
            unit_price = None
            tax_code_ref_value = None
            tax_code_ref_name = None
            service_date = None
            discount_rate = None
            discount_amt = None
            
            if line.detail_type == "SalesItemLineDetail" and line.sales_item_line_detail:
                detail = line.sales_item_line_detail
                item_ref_value = detail.item_ref.value if detail.item_ref else None
                item_ref_name = detail.item_ref.name if detail.item_ref else None
                class_ref_value = detail.class_ref.value if detail.class_ref else None
                class_ref_name = detail.class_ref.name if detail.class_ref else None
                qty = detail.qty
                unit_price = detail.unit_price
                tax_code_ref_value = detail.tax_code_ref.value if detail.tax_code_ref else None
                tax_code_ref_name = detail.tax_code_ref.name if detail.tax_code_ref else None
                service_date = detail.service_date
                discount_rate = detail.discount_rate
                discount_amt = detail.discount_amt
            elif line.detail_type == "DiscountLineDetail" and line.discount_line_detail:
                detail = line.discount_line_detail
                if detail.percent_based and detail.discount_percent is not None:
                    discount_rate = detail.discount_percent
                elif detail.discount_percent is not None:
                    discount_amt = detail.discount_percent
            
            # Check if line exists (use pre-loaded cache if available)
            existing_line = None
            if line.id:
                if existing_lines_map is not None:
                    existing_line = existing_lines_map.get((qbo_invoice_id, line.id))
                else:
                    existing_line = self.line_repo.read_by_qbo_invoice_id_and_qbo_line_id(
                        qbo_invoice_id=qbo_invoice_id,
                        qbo_line_id=line.id
                    )
            
            if existing_line:
                self.line_repo.update_by_id(
                    id=existing_line.id,
                    row_version=existing_line.row_version_bytes,
                    line_num=line.line_num,
                    description=line.description,
                    amount=line.amount,
                    detail_type=line.detail_type,
                    item_ref_value=item_ref_value,
                    item_ref_name=item_ref_name,
                    class_ref_value=class_ref_value,
                    class_ref_name=class_ref_name,
                    qty=qty,
                    unit_price=unit_price,
                    tax_code_ref_value=tax_code_ref_value,
                    tax_code_ref_name=tax_code_ref_name,
                    service_date=service_date,
                    discount_rate=discount_rate,
                    discount_amt=discount_amt,
                )
            else:
                new_line = self.line_repo.create(
                    qbo_invoice_id=qbo_invoice_id,
                    qbo_line_id=line.id,
                    line_num=line.line_num,
                    description=line.description,
                    amount=line.amount,
                    detail_type=line.detail_type,
                    item_ref_value=item_ref_value,
                    item_ref_name=item_ref_name,
                    class_ref_value=class_ref_value,
                    class_ref_name=class_ref_name,
                    qty=qty,
                    unit_price=unit_price,
                    tax_code_ref_value=tax_code_ref_value,
                    tax_code_ref_name=tax_code_ref_name,
                    service_date=service_date,
                    discount_rate=discount_rate,
                    discount_amt=discount_amt,
                )
                # Update cache so a retry or second pass on the same invoice doesn't duplicate the line
                if existing_lines_map is not None and line.id:
                    existing_lines_map[(qbo_invoice_id, line.id)] = new_line

        # Delete stale lines — any locally-stored QboInvoiceLine whose qbo_line_id is
        # no longer present in the QBO API response means QBO removed that line.
        # Delete the InvoiceLineItemInvoiceLine mapping first (FK constraint), then the line.
        from integrations.intuit.qbo.invoice.connector.invoice_line_item.persistence.repo import InvoiceLineItemInvoiceLineRepository
        mapping_repo = InvoiceLineItemInvoiceLineRepository()
        stored_lines = self.line_repo.read_by_qbo_invoice_id(qbo_invoice_id)
        for stored_line in stored_lines:
            if stored_line.qbo_line_id not in current_qbo_line_ids:
                logger.info(
                    f"Deleting stale QboInvoiceLine id={stored_line.id} "
                    f"qbo_line_id={stored_line.qbo_line_id} (no longer in QBO response)"
                )
                try:
                    stale_mapping = mapping_repo.read_by_qbo_invoice_line_id(stored_line.id)
                    if stale_mapping:
                        mapping_repo.delete_by_id(stale_mapping.id)
                        logger.info(f"Deleted stale InvoiceLineItemInvoiceLine mapping id={stale_mapping.id}")
                except Exception as e:
                    logger.warning(f"Could not delete stale mapping for QboInvoiceLine {stored_line.id}: {e}")
                try:
                    self.line_repo.delete_by_id(stored_line.id)
                    # Remove from cache so subsequent passes don't try to use the deleted line
                    if existing_lines_map is not None and stored_line.qbo_line_id:
                        existing_lines_map.pop((qbo_invoice_id, stored_line.qbo_line_id), None)
                except Exception as e:
                    logger.warning(f"Could not delete stale QboInvoiceLine {stored_line.id}: {e}")

    def _sync_to_invoices(self, invoices: List[QboInvoice]) -> None:
        """
        Sync invoices to Invoice module.
        
        Args:
            invoices: List of QboInvoice records
        """
        if not invoices:
            return
        
        # Import here to avoid circular dependencies
        from integrations.intuit.qbo.invoice.connector.invoice.business.service import InvoiceInvoiceConnector

        connector = InvoiceInvoiceConnector()
        connector.preload_caches()

        for invoice in invoices:
            try:
                # Get invoice lines for this invoice
                invoice_lines = self.line_repo.read_by_qbo_invoice_id(invoice.id)
                invoice_module = connector.sync_from_qbo_invoice(invoice, invoice_lines)
                logger.info(f"Synced QboInvoice {invoice.id} to Invoice {invoice_module.id}")
            except Exception as e:
                logger.error(f"Failed to sync QboInvoice {invoice.id} to Invoice: {e}")

    def read_all(self) -> List[QboInvoice]:
        """
        Read all QboInvoices.
        """
        return self.repo.read_all()

    def read_by_realm_id(self, realm_id: str) -> List[QboInvoice]:
        """
        Read all QboInvoices by realm ID.
        """
        return self.repo.read_by_realm_id(realm_id)

    def read_by_qbo_id(self, qbo_id: str) -> Optional[QboInvoice]:
        """
        Read a QboInvoice by QBO ID.
        """
        return self.repo.read_by_qbo_id(qbo_id)

    def read_by_id(self, id: int) -> Optional[QboInvoice]:
        """
        Read a QboInvoice by database ID.
        """
        return self.repo.read_by_id(id)

    def read_lines_by_qbo_invoice_id(self, qbo_invoice_id: int) -> List[QboInvoiceLine]:
        """
        Read all QboInvoiceLines for a QboInvoice.
        """
        return self.line_repo.read_by_qbo_invoice_id(qbo_invoice_id)
