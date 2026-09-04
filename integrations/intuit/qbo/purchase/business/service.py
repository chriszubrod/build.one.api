# Python Standard Library Imports
import logging
from typing import List, Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.purchase.business.model import QboPurchase, QboPurchaseLine
from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseRepository, QboPurchaseLineRepository
from integrations.intuit.qbo.purchase.external.client import QboPurchaseClient
from integrations.intuit.qbo.purchase.external.schemas import QboPurchase as QboPurchaseExternalSchema
from integrations.intuit.qbo.base.pacing import pace_batch
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome, project_records
from shared.authz import current_user_id, current_is_system_admin
from shared.database import with_retry

logger = logging.getLogger(__name__)

# Sync configuration
MAX_RETRIES = 3  # Max retries for transient errors
INITIAL_RETRY_DELAY = 2.0  # Initial retry delay (seconds)


def _clear_legacy_purchase_line_expense_line_item_mapping_by_qbo_line_id(qbo_purchase_line_id: int) -> None:
    """U-364 deploy-gap bridge for QboPurchaseService's stale-line cleanup
    (_upsert_purchase_lines) and deleted-purchase reconcile
    (_reconcile_deleted_purchases) — see their call sites. Raw SQL, not a
    repo/model (both retired in this unit): deletes any row in the (soon-to-
    be-dropped) qbo.PurchaseLineExpenseLineItem table that still points at
    this staging QboPurchaseLine, so its NO ACTION FK
    (FK_PurchaseLineExpenseLineItem_QboPurchaseLine) never blocks the staging
    line's delete. Same OBJECT_ID-guard idiom as entities/expense_line_item/
    business/service.py's sibling bridge (and U-363's bill precedent) —
    table-already-dropped becomes a plain SQL no-op, not a caught Python
    exception. Once /em applies the DROP, this whole function becomes a
    permanent no-op and should be deleted."""
    from shared.database import get_connection

    try:
        with get_connection() as conn:
            conn.cursor().execute(
                "IF OBJECT_ID('qbo.PurchaseLineExpenseLineItem', 'U') IS NOT NULL "
                "DELETE FROM [qbo].[PurchaseLineExpenseLineItem] WHERE [QboPurchaseLineId] = ?",
                (qbo_purchase_line_id,),
            )
    except Exception as e:
        # Best-effort only — the real safety net is the FK itself. If a mapping
        # row really does still exist and this failed to clear it, the stale
        # QboPurchaseLine delete below 547s and is caught by its own try/except
        # (fail-safe: the stale row just persists for the next pull to retry,
        # never fail-silent-corruption).
        logger.warning(
            f"Could not clear legacy qbo.PurchaseLineExpenseLineItem mapping for "
            f"QboPurchaseLine {qbo_purchase_line_id}: {e}"
        )


class QboPurchaseService:
    """
    Service for QboPurchase entity business operations.
    """

    def __init__(
        self,
        repo: Optional[QboPurchaseRepository] = None,
        line_repo: Optional[QboPurchaseLineRepository] = None,
    ):
        """Initialize the QboPurchaseService."""
        self.repo = repo or QboPurchaseRepository()
        self.line_repo = line_repo or QboPurchaseLineRepository()

    def sync_from_qbo(
        self,
        realm_id: str,
        last_updated_time: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sync_to_modules: bool = False,
        reconcile_deletes: bool = False,
    ) -> SyncOutcome[QboPurchase]:
        """
        Fetch Purchases from QBO API and store locally.
        Uses upsert pattern: creates if not exists, updates if exists.
        
        Args:
            realm_id: QBO company realm ID
            last_updated_time: Optional ISO format datetime string. If provided, only fetches
                Purchases where Metadata.LastUpdatedTime > last_updated_time.
            start_date: Optional date string (YYYY-MM-DD). If provided, only fetches
                Purchases where TxnDate >= start_date.
            end_date: Optional date string (YYYY-MM-DD). If provided, only fetches
                Purchases where TxnDate <= end_date.
            sync_to_modules: If True, also sync to Expense/ExpenseLineItem modules
            reconcile_deletes: If True, reconcile deletes (full sync only)
        
        Returns:
            SyncOutcome[QboPurchase]: Pull run envelope including synced staging rows
        """
        outcome: SyncOutcome[QboPurchase] = SyncOutcome.for_service_pull()
        self._realm_id = realm_id

        # Fetch Purchases from QBO API. QboHttpClient (via QboPurchaseClient) resolves
        # and refreshes the access token lazily, so no upfront auth call is needed.
        with QboPurchaseClient(realm_id=realm_id) as client:
            qbo_purchases: List[QboPurchaseExternalSchema] = client.query_all_purchases(
                last_updated_time=last_updated_time,
                start_date=start_date,
                end_date=end_date,
            )

        outcome.fetched = len(qbo_purchases)
        if not qbo_purchases:
            logger.info(f"No Purchases found since {last_updated_time or 'beginning'}")
            return outcome
        
        logger.info(f"Retrieved {len(qbo_purchases)} purchases from QBO")
        
        # Process each purchase with retry logic and batch delays

        for i, qbo_purchase in enumerate(qbo_purchases):
            try:
                # Use retry logic for transient database errors
                local_purchase = with_retry(
                    self._upsert_purchase,
                    qbo_purchase,
                    realm_id,
                    max_retries=MAX_RETRIES,
                    initial_delay=INITIAL_RETRY_DELAY,
                )
                outcome.record_synced(local_purchase)
                logger.debug(f"Upserted purchase {qbo_purchase.id} ({i + 1}/{len(qbo_purchases)})")
            except Exception as e:
                logger.error(f"Failed to upsert purchase {qbo_purchase.id}: {e}")
                outcome.record_staging_failure(qbo_purchase.id, e)
            
            # Add delay between batches to prevent connection exhaustion.
            # Token refresh is handled automatically by QboHttpClient on each request.
            pace_batch(i, len(qbo_purchases), logger, "purchases")
        
        if outcome.staging_failed_ids:
            logger.warning(
                f"Failed to upsert {len(outcome.staging_failed_ids)} purchases: {outcome.staging_failed_ids}"
            )
        
        # Sync to modules if requested
        if sync_to_modules:
            self._sync_to_expenses(outcome.synced, outcome)

        # Delete reconciliation: only valid on a full, unfiltered sync so we have
        # a complete picture of what QBO currently holds.
        if reconcile_deletes and last_updated_time is None and not start_date and not end_date:
            self._reconcile_deleted_purchases(realm_id)

        return outcome

    def upsert_from_external(
        self, qbo_purchase: QboPurchaseExternalSchema, realm_id: str,
    ) -> tuple[QboPurchase, List[QboPurchaseLine]]:
        """
        Persist an external-schema QboPurchase (+ its inline lines) into the local
        cache and return the stored dataclass form. See QboBillService.upsert_from_external
        for the rationale — connectors expect the flat dataclass shape.
        """
        local_purchase = self._upsert_purchase(qbo_purchase, realm_id)
        lines = self.line_repo.read_by_qbo_purchase_id(local_purchase.id)
        return local_purchase, lines

    def _upsert_purchase(self, qbo_purchase: QboPurchaseExternalSchema, realm_id: str) -> QboPurchase:
        """
        Create or update a QboPurchase record along with its line items.
        
        Args:
            qbo_purchase: QBO Purchase from external API
            realm_id: QBO realm ID
        
        Returns:
            QboPurchase: The created or updated record
        """
        # Check if purchase already exists
        existing = self.repo.read_by_qbo_id_and_realm_id(qbo_id=qbo_purchase.id, realm_id=realm_id)
        
        # Extract reference fields
        account_ref_value = qbo_purchase.account_ref.value if qbo_purchase.account_ref else None
        account_ref_name = qbo_purchase.account_ref.name if qbo_purchase.account_ref else None
        entity_ref_value = qbo_purchase.entity_ref.value if qbo_purchase.entity_ref else None
        entity_ref_name = qbo_purchase.entity_ref.name if qbo_purchase.entity_ref else None
        currency_ref_value = qbo_purchase.currency_ref.value if qbo_purchase.currency_ref else None
        currency_ref_name = qbo_purchase.currency_ref.name if qbo_purchase.currency_ref else None
        department_ref_value = qbo_purchase.department_ref.value if qbo_purchase.department_ref else None
        department_ref_name = qbo_purchase.department_ref.name if qbo_purchase.department_ref else None
        
        update_kwargs = dict(
            qbo_id=qbo_purchase.id,
            sync_token=qbo_purchase.sync_token,
            realm_id=realm_id,
            payment_type=qbo_purchase.payment_type,
            account_ref_value=account_ref_value,
            account_ref_name=account_ref_name,
            entity_ref_value=entity_ref_value,
            entity_ref_name=entity_ref_name,
            credit=qbo_purchase.credit,
            txn_date=qbo_purchase.txn_date,
            doc_number=qbo_purchase.doc_number,
            private_note=qbo_purchase.private_note,
            total_amt=qbo_purchase.total_amt,
            currency_ref_value=currency_ref_value,
            currency_ref_name=currency_ref_name,
            exchange_rate=qbo_purchase.exchange_rate,
            department_ref_value=department_ref_value,
            department_ref_name=department_ref_name,
            global_tax_calculation=qbo_purchase.global_tax_calculation,
        )

        if existing:
            # Update existing record
            logger.debug(f"Updating existing QBO purchase {qbo_purchase.id}")
            local_purchase = self.repo.update_by_qbo_id(
                row_version=existing.row_version_bytes,
                **update_kwargs,
            )
            if local_purchase is None:
                # RowVersion conflict — re-read fresh and retry once
                logger.warning(f"RowVersion conflict updating QBO purchase {qbo_purchase.id}, retrying with fresh row_version")
                refreshed = self.repo.read_by_qbo_id_and_realm_id(qbo_id=qbo_purchase.id, realm_id=realm_id)
                if not refreshed:
                    raise ValueError(f"QBO purchase {qbo_purchase.id} disappeared during update retry")
                local_purchase = self.repo.update_by_qbo_id(
                    row_version=refreshed.row_version_bytes,
                    **update_kwargs,
                )
                if local_purchase is None:
                    raise ValueError(f"Failed to update QBO purchase {qbo_purchase.id} after RowVersion retry")
        else:
            # Create new record
            logger.debug(f"Creating new QBO purchase {qbo_purchase.id}")
            local_purchase = self.repo.create(
                qbo_id=qbo_purchase.id,
                sync_token=qbo_purchase.sync_token,
                realm_id=realm_id,
                payment_type=qbo_purchase.payment_type,
                account_ref_value=account_ref_value,
                account_ref_name=account_ref_name,
                entity_ref_value=entity_ref_value,
                entity_ref_name=entity_ref_name,
                credit=qbo_purchase.credit,
                txn_date=qbo_purchase.txn_date,
                doc_number=qbo_purchase.doc_number,
                private_note=qbo_purchase.private_note,
                total_amt=qbo_purchase.total_amt,
                currency_ref_value=currency_ref_value,
                currency_ref_name=currency_ref_name,
                exchange_rate=qbo_purchase.exchange_rate,
                department_ref_value=department_ref_value,
                department_ref_name=department_ref_name,
                global_tax_calculation=qbo_purchase.global_tax_calculation,
            )
        
        # Upsert line items
        if qbo_purchase.line:
            self._upsert_purchase_lines(local_purchase.id, qbo_purchase.line)
        
        return local_purchase

    def _upsert_purchase_lines(self, qbo_purchase_id: int, lines: list) -> None:
        """
        Upsert purchase line items.

        After inserting/updating all lines present in the QBO API response,
        any locally-stored QboPurchaseLine whose qbo_line_id is NOT in the
        current response is stale (line was removed in QBO). Stale lines are
        deleted; U-364's deploy-gap bridge clears any legacy
        PurchaseLineExpenseLineItem mapping row first (see below).

        Args:
            qbo_purchase_id: Database ID of the QboPurchase
            lines: List of QboPurchaseLine from external API
        """
        current_qbo_line_ids = {line.id for line in lines if line.id}

        for line in lines:
            if not line.id:
                logger.debug(f"Skipping QBO purchase line with no Id (detail_type={line.detail_type}, amount={line.amount})")
                continue

            # Extract detail-specific fields based on detail type
            item_ref_value = None
            item_ref_name = None
            account_ref_value = None
            account_ref_name = None
            customer_ref_value = None
            customer_ref_name = None
            class_ref_value = None
            class_ref_name = None
            billable_status = None
            qty = None
            unit_price = None
            markup_percent = None
            
            if line.detail_type == "ItemBasedExpenseLineDetail" and line.item_based_expense_line_detail:
                detail = line.item_based_expense_line_detail
                item_ref_value = detail.item_ref.value if detail.item_ref else None
                item_ref_name = detail.item_ref.name if detail.item_ref else None
                customer_ref_value = detail.customer_ref.value if detail.customer_ref else None
                customer_ref_name = detail.customer_ref.name if detail.customer_ref else None
                class_ref_value = detail.class_ref.value if detail.class_ref else None
                class_ref_name = detail.class_ref.name if detail.class_ref else None
                billable_status = detail.billable_status
                qty = detail.qty
                unit_price = detail.unit_price
                # Extract markup percent from MarkupInfo
                if detail.markup_info and isinstance(detail.markup_info, dict):
                    markup_percent = detail.markup_info.get("Percent")
            elif line.detail_type == "AccountBasedExpenseLineDetail" and line.account_based_expense_line_detail:
                detail = line.account_based_expense_line_detail
                account_ref_value = detail.account_ref.value if detail.account_ref else None
                account_ref_name = detail.account_ref.name if detail.account_ref else None
                customer_ref_value = detail.customer_ref.value if detail.customer_ref else None
                customer_ref_name = detail.customer_ref.name if detail.customer_ref else None
                class_ref_value = detail.class_ref.value if detail.class_ref else None
                class_ref_name = detail.class_ref.name if detail.class_ref else None
                billable_status = detail.billable_status
                # Extract markup percent from MarkupInfo
                if detail.markup_info and isinstance(detail.markup_info, dict):
                    markup_percent = detail.markup_info.get("Percent")
            
            # Check if line exists
            existing_line = None
            if line.id:
                existing_line = self.line_repo.read_by_qbo_purchase_id_and_qbo_line_id(
                    qbo_purchase_id=qbo_purchase_id,
                    qbo_line_id=line.id
                )
            
            if existing_line:
                # Update existing line
                self.line_repo.update_by_id(
                    id=existing_line.id,
                    row_version=existing_line.row_version_bytes,
                    line_num=line.line_num,
                    description=line.description,
                    amount=line.amount,
                    detail_type=line.detail_type,
                    item_ref_value=item_ref_value,
                    item_ref_name=item_ref_name,
                    account_ref_value=account_ref_value,
                    account_ref_name=account_ref_name,
                    customer_ref_value=customer_ref_value,
                    customer_ref_name=customer_ref_name,
                    class_ref_value=class_ref_value,
                    class_ref_name=class_ref_name,
                    billable_status=billable_status,
                    qty=qty,
                    unit_price=unit_price,
                    markup_percent=markup_percent,
                )
            else:
                # Create new line
                self.line_repo.create(
                    qbo_purchase_id=qbo_purchase_id,
                    qbo_line_id=line.id,
                    line_num=line.line_num,
                    description=line.description,
                    amount=line.amount,
                    detail_type=line.detail_type,
                    item_ref_value=item_ref_value,
                    item_ref_name=item_ref_name,
                    account_ref_value=account_ref_value,
                    account_ref_name=account_ref_name,
                    customer_ref_value=customer_ref_value,
                    customer_ref_name=customer_ref_name,
                    class_ref_value=class_ref_value,
                    class_ref_name=class_ref_name,
                    billable_status=billable_status,
                    qty=qty,
                    unit_price=unit_price,
                    markup_percent=markup_percent,
                )

        # Stale lines — any locally-stored QboPurchaseLine whose qbo_line_id is no longer
        # present in the QBO API response means QBO removed (or regenerated the id of) that
        # line. The dbo ExpenseLineItem is preserved (never deleted here) — U-364: the
        # downstream ExpenseLineItem is matched by its own dbo-native (ExpenseId, QboId)
        # identity now — the connector-level qbo.PurchaseLineExpenseLineItem mapping this
        # layer used to keep valid is retired. The TABLE itself is not dropped by this
        # unit though, and it carries a live NO ACTION FK onto this staging row
        # (FK_PurchaseLineExpenseLineItem_QboPurchaseLine) — so the delete below still
        # needs the mapping cleared first, via the deploy-gap bridge, or it 547s and the
        # stale row silently survives. Its dbo ExpenseLineItem, if any, is left under its
        # now-stale identity — the re-adopt matcher (base/line_orphan_adopt.py) picks it
        # up on a future pull if QBO re-sends an equivalent line; nothing here can
        # determine that on its own.
        stored_lines = self.line_repo.read_by_qbo_purchase_id(qbo_purchase_id)
        for stored_line in stored_lines:
            if stored_line.qbo_line_id not in current_qbo_line_ids:
                logger.info(
                    f"Deleting stale QboPurchaseLine id={stored_line.id} "
                    f"qbo_line_id={stored_line.qbo_line_id} (no longer in QBO response)"
                )
                _clear_legacy_purchase_line_expense_line_item_mapping_by_qbo_line_id(stored_line.id)
                try:
                    self.line_repo.delete_by_id(stored_line.id)
                except Exception as e:
                    logger.warning(f"Could not delete stale QboPurchaseLine {stored_line.id}: {e}")

    def _reconcile_deleted_purchases(
        self,
        realm_id: str,
    ) -> int:
        """
        Delete local records for QBO purchases that no longer exist in QBO.

        Only called on full syncs (no last_updated_time / date filters).
        U-212: diffs against the strict id pager with ceiling + GET-confirm —
        see base/delete_reconcile.py. An aborted gate deletes nothing.

        Order of deletion (respects FK NO ACTION constraints added by the FK migration):
          1. PurchaseLineExpenseLineItem mapping rows for the purchase's lines (U-364
             deploy-gap bridge — the table is not yet dropped).
          2. Delete the Expense, resolved directly via dbo-native QBO identity (U-354 —
             no more qbo.PurchaseExpense mapping to hop through). App-layer cascade:
             ExpenseLineItem → attachment → blob.
          3. Delete the QboPurchase — FK_QboPurchaseLine_QboPurchase CASCADE handles
             qbo.PurchaseLine rows.
        """
        from integrations.intuit.qbo.base.delete_reconcile import (
            record_partial_delete_issue,
            strict_confirmed_deleted_ids,
        )
        from integrations.intuit.qbo.base.ids import normalize_qbo_id
        from entities.expense.business.service import ExpenseService

        local_purchases = self.repo.read_by_realm_id(realm_id)

        with QboPurchaseClient(realm_id=realm_id) as client:
            confirmed = strict_confirmed_deleted_ids(
                entity_type="Purchase",
                realm_id=realm_id,
                fetch_live_ids=client.query_all_purchase_ids,
                confirm_get=client.get_purchase,
                local_qbo_ids=[p.qbo_id for p in local_purchases],
            )
        if not confirmed:
            return 0

        expense_service = ExpenseService()

        deleted = 0
        for local in local_purchases:
            if normalize_qbo_id(local.qbo_id) not in confirmed:
                continue

            logger.warning(
                f"QboPurchase qbo_id={local.qbo_id} (local id={local.id}) confirmed deleted in QBO — "
                f"deleting local record and mapped Expense"
            )
            # U-354: a list, not a bool — Step 2 now deletes the Expense header
            # directly (no mapping row involved), so this must also cover THAT
            # destructive action; otherwise a run with no line mappings but a
            # successful Expense delete would skip the partial-delete issue below
            # if Step 3 then failed. Set BEFORE each attempt, not after it
            # succeeds: a raise partway through delete_by_public_id's own cascade
            # (line items/attachments/blobs) is itself a real partial-delete, and
            # must not be lost just because the call that started it never
            # returned. Mirrors QboVendorCreditService's identical U-353 fix.
            destructive_labels = []
            try:
                # Step 1: line mappings (before ExpenseLineItem deletion — FK NO ACTION).
                for line in self.line_repo.read_by_qbo_purchase_id(local.id):
                    destructive_labels.append("PurchaseLineExpenseLineItem mapping")
                    _clear_legacy_purchase_line_expense_line_item_mapping_by_qbo_line_id(line.id)

                # Step 2: the Expense, resolved directly via dbo-native QBO identity
                # (U-354 — no more qbo.PurchaseExpense mapping row to hop through).
                expense = expense_service.read_by_qbo_identity(local.qbo_id, realm_id)
                if expense:
                    destructive_labels.append("Expense header")
                    expense_service.delete_by_public_id(expense.public_id)
                    logger.info(
                        f"Deleted Expense id={expense.id} mapped to deleted QboPurchase {local.qbo_id}"
                    )

                # Step 3: Delete the QboPurchase.
                # FK_QboPurchaseLine_QboPurchase ON DELETE CASCADE handles PurchaseLine deletion.
                self.repo.delete_by_qbo_id(local.qbo_id)
                logger.info(f"Deleted QboPurchase qbo_id={local.qbo_id}")
                deleted += 1
            except Exception as e:
                logger.error(f"Failed to delete stale QboPurchase {local.qbo_id}: {e}")
                if destructive_labels:
                    # dict.fromkeys(...) dedupes while preserving first-seen order — a
                    # purchase with 3+ mapped lines would otherwise repeat "PurchaseLine
                    # ExpenseLineItem mapping" once per line, turning the recorded issue's
                    # label into unreadable noise instead of a clean summary.
                    record_partial_delete_issue(
                        entity_type="Purchase",
                        mapping_label=" + ".join(dict.fromkeys(destructive_labels)),
                        mapped_label="Expense",
                        realm_id=realm_id,
                        qbo_id=local.qbo_id,
                        local_id=local.id,
                        error=e,
                    )

        if deleted:
            logger.info(f"Reconciled {deleted} deleted QBO purchase(s) for realm {realm_id}")
        return deleted

    def _sync_to_expenses(self, purchases: List[QboPurchase], outcome: SyncOutcome) -> None:
        """
        Sync purchases to Expense module. Attachables are synced in the main sync loop.
        
        Args:
            purchases: List of QboPurchase records
        """
        if not purchases:
            return
        
        # Import here to avoid circular dependencies
        from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector

        connector = PurchaseExpenseConnector()

        def _project_purchase(purchase):
            purchase_lines = self.line_repo.read_by_qbo_purchase_id(purchase.id)
            return connector.sync_from_qbo_purchase(purchase, purchase_lines)

        project_records(
            purchases,
            outcome,
            label="Purchase->Expense",
            project_one=_project_purchase,
            logger=logger,
        )

    def read_all(self) -> List[QboPurchase]:
        """
        Read all QboPurchases.
        """
        return self.repo.read_all()

    def read_by_realm_id(self, realm_id: str) -> List[QboPurchase]:
        """
        Read all QboPurchases by realm ID.
        """
        return self.repo.read_by_realm_id(realm_id)

    def read_by_qbo_id(self, qbo_id: str) -> Optional[QboPurchase]:
        """
        Read a QboPurchase by QBO ID.
        """
        return self.repo.read_by_qbo_id(qbo_id)

    def read_by_id(self, id: int) -> Optional[QboPurchase]:
        """
        Read a QboPurchase by database ID.
        """
        return self.repo.read_by_id(id)

    def read_lines_by_qbo_purchase_id(self, qbo_purchase_id: int) -> List[QboPurchaseLine]:
        """
        Read all QboPurchaseLines for a QboPurchase.
        """
        return self.line_repo.read_by_qbo_purchase_id(qbo_purchase_id)

    def get_expense_coding_queue(self, realm_id: Optional[str] = None) -> List[dict]:
        """
        Read the strict 58999 coding queue and idempotently seed missing
        ExpenseCodingItem rows so every returned line carries coding state.
        """
        actor_user_id = current_user_id.get()
        actor_is_system_admin = current_is_system_admin.get()
        rows = self.line_repo.read_expense_coding_queue(
            realm_id=realm_id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
        )
        needs_reseed = any(row.get("coding_item_public_id") is None for row in rows)
        if not needs_reseed:
            return rows

        from entities.expense_coding_item.business.service import ExpenseCodingItemService

        coding_service = ExpenseCodingItemService()
        for row in rows:
            if row.get("coding_item_public_id") is not None:
                continue
            coding_service.upsert_from_queue(
                qbo_purchase_id=row["qbo_purchase_id"],
                qbo_purchase_line_id=row["qbo_purchase_line_id"],
                qbo_line_id=row.get("qbo_line_id"),
                qbo_purchase_qbo_id=row.get("qbo_purchase_qbo_id"),
                realm_id=row.get("realm_id"),
                vendor_qbo_id=row.get("vendor_qbo_id"),
            )

        return self.line_repo.read_expense_coding_queue(
            realm_id=realm_id,
            actor_user_id=actor_user_id,
            actor_is_system_admin=actor_is_system_admin,
        )

    def get_expense_coding_metrics(
        self,
        realm_id: Optional[str] = None,
        since_days: Optional[int] = None,
    ) -> dict:
        return self.line_repo.read_expense_coding_metrics(
            realm_id=realm_id,
            since_days=since_days,
            actor_user_id=current_user_id.get(),
            actor_is_system_admin=current_is_system_admin.get(),
        )
