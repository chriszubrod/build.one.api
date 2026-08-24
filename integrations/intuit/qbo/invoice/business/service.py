# Python Standard Library Imports
import logging
from typing import List, Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.invoice.business.model import QboInvoice, QboInvoiceLine
from integrations.intuit.qbo.invoice.persistence.repo import QboInvoiceRepository, QboInvoiceLineRepository
from integrations.intuit.qbo.invoice.external.client import QboInvoiceClient
from integrations.intuit.qbo.invoice.external.schemas import QboInvoice as QboInvoiceExternalSchema
from integrations.intuit.qbo.base.pacing import pace_batch
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome, project_records
from shared.database import with_retry

logger = logging.getLogger(__name__)

# Sync configuration
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
        # Per-instance memo (U-307a) for _resolve_cost_code_for_qbo_item_ref, keyed
        # by (realm_id, qbo_item_ref_value) — amortizes across an entire project's
        # draw rollup (draw_financials.py reuses one QboInvoiceService instance for
        # every invoice), the same DB-round-trip concern that motivated U-292's
        # original bulk index.
        self._cost_code_resolution_cache: dict = {}

    def sync_from_qbo(
        self,
        realm_id: str,
        last_updated_time: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        customer_ref: Optional[str] = None,
        sync_to_modules: bool = False,
    ) -> SyncOutcome[QboInvoice]:
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
            SyncOutcome[QboInvoice]: Pull run envelope including synced staging rows
        """
        outcome: SyncOutcome[QboInvoice] = SyncOutcome.for_service_pull()
        # Fetch Invoices from QBO API. QboHttpClient (via QboInvoiceClient) resolves
        # and refreshes the access token lazily, so no upfront auth call is needed.
        with QboInvoiceClient(realm_id=realm_id) as client:
            qbo_invoices: List[QboInvoiceExternalSchema] = client.query_all_invoices(
                last_updated_time=last_updated_time,
                start_date=start_date,
                end_date=end_date,
                customer_ref=customer_ref,
            )

        outcome.fetched = len(qbo_invoices)
        if not qbo_invoices:
            logger.info(f"No Invoices found since {last_updated_time or 'beginning'}")
            return outcome
        
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
        changed_invoices = []  # Only invoices actually modified (not sync_token-skipped)

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
                outcome.record_synced(local_invoice)
                # Only propagate to modules if the invoice was actually created or updated
                if existing_before is None or existing_before.sync_token != qbo_invoice.sync_token:
                    changed_invoices.append(local_invoice)
                logger.debug(f"Upserted invoice {qbo_invoice.id} ({i + 1}/{len(qbo_invoices)})")
            except Exception as e:
                logger.error(f"Failed to upsert invoice {qbo_invoice.id}: {e}")
                outcome.record_staging_failure(qbo_invoice.id, e)

            pace_batch(i, len(qbo_invoices), logger, "invoices")
        
        if outcome.staging_failed_ids:
            logger.warning(
                f"Failed to upsert {len(outcome.staging_failed_ids)} invoices: {outcome.staging_failed_ids}"
            )
        
        # Sync to modules if requested (only changed invoices — skipped ones haven't changed)
        if sync_to_modules:
            self._sync_to_invoices(changed_invoices, outcome)
        
        return outcome

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

            # First LinkedTxn entry — the deterministic source-linkage key
            # (typically TxnType='ReimburseCharge' for lines created from
            # billable expenses; the RC's own LinkedTxn names the source
            # Bill/Purchase). Stored so reconciliation can resolve invoice
            # lines to local sources by ID instead of fingerprinting.
            linked_txn_type = None
            linked_txn_id = None
            line_linked = getattr(line, "linked_txn", None)
            if line_linked:
                first_linked = line_linked[0]
                linked_txn_type = getattr(first_linked, "txn_type", None)
                raw_txn_id = getattr(first_linked, "txn_id", None)
                linked_txn_id = str(raw_txn_id) if raw_txn_id else None
            
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
                    linked_txn_type=linked_txn_type,
                    linked_txn_id=linked_txn_id,
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
                    linked_txn_type=linked_txn_type,
                    linked_txn_id=linked_txn_id,
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

    def _sync_to_invoices(self, invoices: List[QboInvoice], outcome: SyncOutcome) -> None:
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

        def _project_invoice(invoice):
            invoice_lines = self.line_repo.read_by_qbo_invoice_id(invoice.id)
            return connector.sync_from_qbo_invoice(invoice, invoice_lines)

        project_records(
            invoices,
            outcome,
            label="Invoice->Invoice",
            project_one=_project_invoice,
            logger=logger,
        )

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

    def cost_coded_lines_for_invoice(self, invoice_id: int) -> List[tuple]:
        """
        (cost_code_number, cost_code_name, amount) triples for a LOCAL dbo Invoice's
        mapped QBO lines, resolved by ID (QboItem -> ItemSubCostCode -> SubCostCode ->
        CostCode) — never by parsing the QBO Item's display name/hierarchy. A line
        whose item has no resolvable cost code comes back as ("", "Uncoded", amount)
        so callers can still foot a column to the invoice total. SubTotal restatement
        lines and lines with no amount are skipped. Returns [] when the invoice has
        no QBO mapping or no lines (U-292 — the dbo-native seam draw_financials.py
        consumes in place of its former ItemRefName parser).

        U-284: resolves the staging-side QboInvoice off dbo.Invoice's own native
        QboId/RealmId (U-238a) as the fast path, falling back to the
        qbo.InvoiceInvoice mapping table on a miss — mirrors every Python-side
        fast path in this program (identity_fastpath.py's hit=False contract).
        A dbo-identity miss (unbackfilled QboId, or a stale/theft-cleared
        identity whose mapping row is still intact) is NOT the same as "never
        synced to QBO"; treating it as such would silently drop cost-coded
        lines from the Trend PDF for an invoice that's actually mapped fine.
        qbo.Invoice/qbo.InvoiceLine stay exactly as they were either way.
        """
        from entities.invoice.business.service import InvoiceService
        from integrations.intuit.qbo.invoice.connector.invoice.persistence.repo import (
            InvoiceInvoiceRepository,
        )

        invoice = InvoiceService().read_by_id(invoice_id)
        realm_id = invoice.realm_id if invoice else None
        qbo_invoice_id = None
        if invoice and invoice.qbo_id:
            qbo_invoice = self.repo.read_by_qbo_id_and_realm_id(invoice.qbo_id, invoice.realm_id)
            if qbo_invoice:
                qbo_invoice_id = qbo_invoice.id
        if qbo_invoice_id is None:
            mapping = InvoiceInvoiceRepository().read_by_invoice_id(invoice_id)
            if not mapping or not mapping.qbo_invoice_id:
                return []
            qbo_invoice_id = mapping.qbo_invoice_id
        lines = self.line_repo.read_by_qbo_invoice_id(qbo_invoice_id)
        if not lines:
            return []

        triples: List[tuple] = []
        for line in lines:
            if (line.detail_type or "") == "SubTotalLineDetail":
                continue
            if line.amount is None:
                continue
            cost_code = self._resolve_cost_code_for_qbo_item_ref(line.item_ref_value, realm_id)
            number, name = cost_code if cost_code else ("", "Uncoded")
            triples.append((number, name, line.amount))
        return triples

    def _resolve_cost_code_for_qbo_item_ref(self, qbo_item_ref_value: Optional[str], realm_id: Optional[str] = None):
        """(cost_code_number, cost_code_name) for a QBO Item reference value, resolved
        by ID via the shared cost_code_resolver (U-307a) rather than by parsing the
        item's display name/hierarchy. Memoized per instance (see
        _cost_code_resolution_cache) so a project with many invoices resolves each
        recurring QBO item once, not per line -- a real many-invoice project hit
        connection drops under a naive per-line-query shape during U-292's own
        equivalence testing against live data, which is why this stayed a cache
        rather than reverting to a point-query-per-line. None when no live QboItem
        or no resolvable cost code -- caller falls back to the Uncoded bucket.

        Most invoice lines carry a SubCostCode-level Item (dbo.SubCostCode.QboId,
        legacy qbo.Item -> qbo.ItemSubCostCode fallback); some carry a
        CostCode-level-only Item with no SubCostCode granularity (e.g. "Initial
        Deposit"), mapped directly via dbo.CostCode.QboId / legacy qbo.ItemCostCode
        instead -- tried as a fallback so that class isn't silently dropped to
        Uncoded (found by U-292's original equivalence proof against real invoice
        lines). The fallback applies whenever the SubCostCode-level resolution
        doesn't actually resolve to a usable numeric cost code (absent, or
        resolving to a dangling/non-numeric CostCode) — checked by resolved VALUE,
        not by whether a SubCostCode-level match exists, so a broken SubCostCode
        can't shadow a perfectly good CostCode-level mapping for the same item. A
        resolved CostCode whose Number has no leading digit (the 2 QBO-admin
        pseudo-codes 'Hours'/'Sales' — not real job-cost categories) is treated as
        unresolved, matching the prior ItemRefName parser's behavior exactly (it
        only ever recognized a numeric-prefixed cost code)."""
        if not qbo_item_ref_value:
            return None

        cache_key = (realm_id, qbo_item_ref_value)
        if cache_key in self._cost_code_resolution_cache:
            return self._cost_code_resolution_cache[cache_key]

        from integrations.intuit.qbo.base.cost_code_resolver import (
            resolve_dbo_sub_cost_code,
            resolve_dbo_cost_code_direct,
        )
        from integrations.intuit.qbo.item.persistence.repo import QboItemRepository
        from integrations.intuit.qbo.item.connector.sub_cost_code.persistence.repo import (
            ItemSubCostCodeRepository,
        )
        from integrations.intuit.qbo.item.connector.cost_code.persistence.repo import (
            ItemCostCodeRepository,
        )
        from entities.sub_cost_code.business.service import SubCostCodeService
        from entities.cost_code.business.service import CostCodeService

        def _numeric_result(cost_code):
            if not cost_code or not (cost_code.number or "")[:1].isdigit():
                return None
            return (cost_code.number, cost_code.name)

        class _MemoizedQboItemRepo:
            """Wraps QboItemRepository so the two legacy-hop fallbacks below (one per
            resolve_dbo_* call) share one qbo.Item lookup instead of each fetching the
            same qbo_item_ref_value over the wire — reached only when BOTH dbo-native
            tiers miss for the same item (a CostCode-level-only item with an
            unstamped identity, or a non-numeric SubCostCode-level hit needing the
            CostCode-level fallback too)."""
            def __init__(self):
                self._repo = QboItemRepository()
                self._cache = {}

            def read_by_qbo_id(self, qbo_id):
                if qbo_id not in self._cache:
                    self._cache[qbo_id] = self._repo.read_by_qbo_id(qbo_id)
                return self._cache[qbo_id]

        sub_cost_code_service = SubCostCodeService()
        cost_code_service = CostCodeService()
        qbo_item_repo = _MemoizedQboItemRepo()

        sub_cost_code = resolve_dbo_sub_cost_code(
            qbo_item_ref_value, realm_id,
            sub_cost_code_service=sub_cost_code_service,
            qbo_item_repo=qbo_item_repo,
            item_sub_cost_code_repo=ItemSubCostCodeRepository(),
        )
        cost_code = cost_code_service.read_by_id(sub_cost_code.cost_code_id) if sub_cost_code else None
        result = _numeric_result(cost_code)

        if result is None:
            fallback_cost_code = resolve_dbo_cost_code_direct(
                qbo_item_ref_value, realm_id,
                cost_code_service=cost_code_service,
                qbo_item_repo=qbo_item_repo,
                item_cost_code_repo=ItemCostCodeRepository(),
            )
            result = _numeric_result(fallback_cost_code)

        self._cost_code_resolution_cache[cache_key] = result
        return result
