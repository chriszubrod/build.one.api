# Python Standard Library Imports
import logging
from typing import Dict, List, Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.purchase.connector.expense.business.model import PurchaseExpense
from integrations.intuit.qbo.purchase.connector.expense.persistence.repo import PurchaseExpenseRepository
from integrations.intuit.qbo.purchase.business.model import QboPurchase, QboPurchaseLine
from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseRepository, QboPurchaseLineRepository
from integrations.intuit.qbo.vendor.connector.vendor.persistence.repo import VendorVendorRepository
from integrations.intuit.qbo.vendor.persistence.repo import QboVendorRepository
from entities.expense.business.service import ExpenseService
from entities.expense.business.model import Expense
from entities.expense_line_item.business.service import ExpenseLineItemService
from entities.vendor.business.service import VendorService
from integrations.intuit.qbo.base.pull_race import guard_lines_present
from integrations.intuit.qbo.base.compensation import rollback_orphan_header
from integrations.intuit.qbo.base.field_ownership import preserve_human_edited_ref, qbo_ref_or_placeholder
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository

logger = logging.getLogger(__name__)


class PurchaseExpenseConnector:
    """
    Connector service for synchronization between QboPurchase and Expense modules.
    """

    def __init__(
        self,
        mapping_repo: Optional[PurchaseExpenseRepository] = None,
        expense_service: Optional[ExpenseService] = None,
        vendor_service: Optional[VendorService] = None,
        vendor_vendor_repo: Optional[VendorVendorRepository] = None,
        qbo_vendor_repo: Optional[QboVendorRepository] = None,
        qbo_purchase_repo: Optional[QboPurchaseRepository] = None,
        qbo_purchase_line_repo: Optional[QboPurchaseLineRepository] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        """Initialize the PurchaseExpenseConnector."""
        self.mapping_repo = mapping_repo or PurchaseExpenseRepository()
        self.expense_service = expense_service or ExpenseService()
        self.vendor_service = vendor_service or VendorService()
        self.vendor_vendor_repo = vendor_vendor_repo or VendorVendorRepository()
        self.qbo_vendor_repo = qbo_vendor_repo or QboVendorRepository()
        self.qbo_purchase_repo = qbo_purchase_repo or QboPurchaseRepository()
        self.qbo_purchase_line_repo = qbo_purchase_line_repo or QboPurchaseLineRepository()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()
        # Per-sync cache: avoids 3 DB round-trips per purchase when multiple purchases
        # share the same QBO vendor (the common case).
        self._vendor_cache: dict = {}
        # Single line connector shared across all purchases so _sub_cost_code_cache and
        # _project_cache persist for the entire sync run, not just per-purchase.
        from integrations.intuit.qbo.purchase.connector.expense_line_item.business.service import PurchaseLineExpenseLineItemConnector
        self._line_connector = PurchaseLineExpenseLineItemConnector()

    def sync_from_qbo_purchase(self, qbo_purchase: QboPurchase, qbo_purchase_lines: List[QboPurchaseLine]) -> Expense:
        """
        Sync data from QboPurchase to Expense module.
        
        This method:
        1. Checks if a mapping exists
        2. Creates or updates the Expense accordingly
        3. Syncs line items to ExpenseLineItem module
        
        Args:
            qbo_purchase: QboPurchase record
            qbo_purchase_lines: List of QboPurchaseLine records for this purchase
        
        Returns:
            Expense: The synced Expense record
        """
        # Find vendor mapping to get Vendor public_id
        # Purchase uses EntityRef instead of VendorRef
        vendor_public_id = self._get_vendor_public_id(qbo_purchase.entity_ref_value)
        if not vendor_public_id:
            raise ValueError(f"No vendor mapping found for QBO entity ref: {qbo_purchase.entity_ref_value}")
        
        # Map QBO Purchase fields to Expense module fields
        reference_number = qbo_ref_or_placeholder(qbo_purchase.doc_number, qbo_purchase.qbo_id)
        expense_date = qbo_purchase.txn_date
        memo = qbo_purchase.private_note
        total_amount = qbo_purchase.total_amt

        # Last-resort guard against the QBO pull-race that mints half-built expenses (see
        # base.pull_race). Pull scripts pre-read past the race; this protects every other caller.
        guard_lines_present(
            qbo_purchase_lines, total_amount,
            entity_label="QboPurchase", entity_id=qbo_purchase.id, qbo_id=qbo_purchase.qbo_id,
        )

        # Check for existing mapping
        mapping = self.mapping_repo.read_by_qbo_purchase_id(qbo_purchase.id)
        
        if mapping:
            # Found existing mapping. Resolve the Expense to update. HEAL-don't-delete
            # (U-029, applying the U-022 CustomerProject pattern): a transient empty-read
            # must NEVER delete the mapping and fall through to CREATE — that would mint a
            # DUPLICATE Expense (the exact hazard U-024 flagged).
            expense = self.expense_service.read_by_id(mapping.expense_id)
            if expense:
                logger.info(f"Updating existing Expense {expense.id} from QboPurchase {qbo_purchase.id}")
                target = expense
            else:
                # Bound Expense read empty. Expense has no unique NAME like Project, and
                # there is no mapping-repoint sproc, so re-resolve by the closest natural
                # fingerprint — (reference_number, vendor) — and heal ONLY when it re-binds
                # the SAME Expense the mapping already targets (a confirmed transient
                # empty-read). See _record_missing_expense_issue for the fingerprint-key
                # rationale.
                replacement = self.expense_service.read_by_reference_number_and_vendor_public_id(
                    reference_number, vendor_public_id
                )
                if replacement and replacement.id == mapping.expense_id:
                    logger.warning(
                        f"Expense {mapping.expense_id} read empty for QboPurchase "
                        f"{qbo_purchase.id} but re-resolved by (reference_number, vendor) — "
                        f"transient empty-read; healing in place, not recreating."
                    )
                    target = replacement
                else:
                    # No fingerprint match, or a match under a DIFFERENT id we cannot safely
                    # repoint to (no mapping-update sproc): preserve the mapping, create
                    # nothing, record a critical reconciliation issue, and RAISE. The purchase
                    # pull treats this ValueError as a permanent skip (watermark advances, sync
                    # stays healthy); the issue is the durable record for follow-up.
                    self._record_missing_expense_issue(
                        qbo_purchase=qbo_purchase, mapping=mapping, fingerprint=replacement
                    )
                    raise ValueError(
                        f"PurchaseExpense mapping {mapping.id} points at missing Expense "
                        f"{mapping.expense_id} and no local Expense fingerprinted by "
                        f"reference_number '{reference_number}' + vendor resolves to it for "
                        f"QboPurchase {qbo_purchase.id}; preserving mapping, skipping."
                    )
            return self._update_existing_expense(
                target,
                qbo_purchase=qbo_purchase,
                vendor_public_id=vendor_public_id,
                expense_date=expense_date,
                reference_number=reference_number,
                memo=memo,
                total_amount=total_amount,
                qbo_purchase_lines=qbo_purchase_lines,
            )

        # Create new Expense
        logger.info(f"Creating new Expense from QboPurchase {qbo_purchase.id}: reference_number={reference_number}")
        expense = self.expense_service.create(
            vendor_public_id=vendor_public_id,
            expense_date=expense_date,
            reference_number=reference_number,
            total_amount=total_amount,
            memo=memo,
            is_draft=False,
            is_credit=qbo_purchase.credit or False,
        )
        
        # Create mapping — if this fails we must roll back the expense we just created,
        # otherwise the unmapped expense will be duplicated on every subsequent sync run.
        expense_id = int(expense.id) if isinstance(expense.id, str) else expense.id
        try:
            mapping = self.create_mapping(expense_id=expense_id, qbo_purchase_id=qbo_purchase.id)
            logger.info(f"Created mapping: Expense {expense_id} <-> QboPurchase {qbo_purchase.id}")
        except Exception as e:
            try:
                self.expense_service.delete_by_public_id(expense.public_id)
                logger.warning(
                    f"Rolled back orphan Expense {expense_id} after mapping failure "
                    f"for QboPurchase {qbo_purchase.id}"
                )
            except Exception as del_e:
                logger.error(f"Could not delete orphan Expense {expense_id}: {del_e}")
            raise ValueError(
                f"Failed to create PurchaseExpense mapping for QboPurchase {qbo_purchase.id}: {e}"
            ) from e
        
        # Compensating rollback — a permanent line failure must not leave a header-only zombie;
        # delete the just-created header + qbo.PurchaseExpense mapping and re-raise (watermark
        # holds; re-pull is idempotent).
        try:
            self._sync_line_items(expense_id, expense.public_id, qbo_purchase_lines, qbo_purchase.realm_id)
        except Exception:
            def _delete_expense_mapping():
                _m = self.mapping_repo.read_by_expense_id(expense_id)
                if _m:
                    self.mapping_repo.delete_by_id(_m.id)
            rollback_orphan_header(
                delete_header=lambda: self.expense_service.delete_by_public_id(expense.public_id),
                delete_mapping=_delete_expense_mapping,
                entity_label='Expense', entity_id=expense_id,
            )
            raise

        return expense

    def _update_existing_expense(
        self,
        expense: Expense,
        *,
        qbo_purchase: QboPurchase,
        vendor_public_id: str,
        expense_date,
        reference_number,
        memo,
        total_amount,
        qbo_purchase_lines: List[QboPurchaseLine],
    ) -> Expense:
        """
        Write the QBO-derived fields onto an existing Expense, then sync its line items.

        Shared by the normal existing-mapping update path and the U-029 heal-in-place
        path so the QboPurchase->Expense field mapping and the reference_number
        preserve/upgrade decision live in exactly one place (no drift between the two
        update sites).

        KI-42 / U-024 (rule of three): never silently revert a human-corrected
        reference_number on re-pull. The shared base helper keeps the stored value
        unless it is empty/null or the QBO-<id> placeholder (which still upgrades to a
        real doc_number when one appears). See base.field_ownership.
        """
        effective_ref = preserve_human_edited_ref(
            expense.reference_number, reference_number, qbo_purchase.qbo_id
        )
        updated = self.expense_service.update_by_public_id(
            expense.public_id,
            row_version=expense.row_version,
            vendor_public_id=vendor_public_id,
            expense_date=expense_date,
            reference_number=effective_ref,
            total_amount=total_amount,
            memo=memo,
            is_draft=False,
            is_credit=qbo_purchase.credit or False,
        )
        self._sync_line_items(updated.id, updated.public_id, qbo_purchase_lines, qbo_purchase.realm_id)
        return updated

    def _record_missing_expense_issue(
        self,
        *,
        qbo_purchase: QboPurchase,
        mapping: PurchaseExpense,
        fingerprint: Optional[Expense] = None,
    ) -> None:
        """
        Record an orphaned-mapping detection on qbo.ReconciliationIssue, failure-
        isolated: a failed insert is logged loud but never breaks the sync (mirrors
        the CustomerProject connector's _raise_missing_project_issue).

        Triggered when a PurchaseExpense mapping exists but its bound Expense read
        empty AND the (reference_number, vendor) fingerprint did not re-resolve to that
        same Expense. We deliberately do NOT delete the mapping or create an Expense
        here — a transient empty-read would otherwise mint a duplicate; the mapping is
        preserved for the next tick / a human to resolve.
        """
        if fingerprint is not None:
            fingerprint_note = (
                f" A different Expense {fingerprint.id} matches the (reference_number, "
                f"vendor) fingerprint but is not the mapped row; not repointing "
                f"(no mapping-update path)."
            )
        else:
            fingerprint_note = (
                " No local Expense matches the (reference_number, vendor) fingerprint."
            )
        details = (
            f"Orphaned PurchaseExpense mapping. Mapping {mapping.id} (QboPurchase "
            f"{qbo_purchase.id}, QboId={qbo_purchase.qbo_id}) points at Expense "
            f"{mapping.expense_id} which no longer reads.{fingerprint_note} Mapping "
            f"preserved; no Expense created. Investigate whether the Expense was "
            f"deleted/renumbered."
        )
        try:
            self.reconciliation_repo.create(
                drift_type="orphaned_purchase_expense_mapping",
                severity="critical",
                action="manual_review",
                entity_type="Expense",
                entity_public_id=None,
                qbo_id=str(qbo_purchase.qbo_id) if qbo_purchase.qbo_id else None,
                realm_id=qbo_purchase.realm_id or "",
                details=details,
            )
            logger.warning(details)
        except Exception as exc:
            # Don't break the sync because the reconciliation insert failed. Log loud.
            logger.error(f"Failed to record reconciliation issue: {exc}. Details: {details}")

    def _get_vendor_public_id(self, qbo_entity_ref_value: str) -> Optional[str]:
        """
        Get the Vendor public_id from QBO entity reference value.
        
        Args:
            qbo_entity_ref_value: QBO entity reference value (QBO Vendor ID)
        
        Returns:
            str: Vendor public_id or None
        """
        if not qbo_entity_ref_value:
            return None

        if qbo_entity_ref_value in self._vendor_cache:
            return self._vendor_cache[qbo_entity_ref_value]

        # First find the QboVendor by qbo_id
        qbo_vendor = self.qbo_vendor_repo.read_by_qbo_id(qbo_entity_ref_value)
        if not qbo_vendor:
            logger.warning(f"QboVendor not found for qbo_id: {qbo_entity_ref_value}")
            self._vendor_cache[qbo_entity_ref_value] = None
            return None

        # Then find the VendorVendor mapping
        vendor_mapping = self.vendor_vendor_repo.read_by_qbo_vendor_id(qbo_vendor.id)
        if not vendor_mapping:
            logger.warning(f"VendorVendor mapping not found for QboVendor ID: {qbo_vendor.id}")
            self._vendor_cache[qbo_entity_ref_value] = None
            return None

        # Get the Vendor
        vendor = self.vendor_service.read_by_id(vendor_mapping.vendor_id)
        if not vendor:
            logger.warning(f"Vendor not found for ID: {vendor_mapping.vendor_id}")
            self._vendor_cache[qbo_entity_ref_value] = None
            return None

        self._vendor_cache[qbo_entity_ref_value] = vendor.public_id
        return vendor.public_id

    def _sync_line_items(self, expense_id: int, expense_public_id: str, qbo_purchase_lines: List[QboPurchaseLine], realm_id: Optional[str] = None) -> None:
        """
        Sync purchase line items to ExpenseLineItem module.

        Args:
            expense_id: Database ID of the Expense
            expense_public_id: Public ID of the Expense (avoids per-line DB read)
            qbo_purchase_lines: List of QboPurchaseLine records
        """
        if not qbo_purchase_lines:
            return

        line_connector = self._line_connector
        failed_line_ids = []

        for qbo_line in qbo_purchase_lines:
            try:
                line_connector.sync_from_qbo_purchase_line(expense_id, expense_public_id, qbo_line, realm_id)
            except Exception as e:
                logger.error(f"Failed to sync QboPurchaseLine {qbo_line.id} to ExpenseLineItem: {e}")
                failed_line_ids.append(qbo_line.id)

        if failed_line_ids:
            # Raise so the whole expense is marked failed (pull watermark holds + retries)
            # rather than silently leaving an expense whose total != sum of its lines.
            raise RuntimeError(
                f"Expense {expense_id}: {len(failed_line_ids)} of {len(qbo_purchase_lines)} "
                f"line item(s) failed to project: {failed_line_ids}"
            )

    def create_mapping(self, expense_id: int, qbo_purchase_id: int) -> PurchaseExpense:
        """
        Create a mapping between Expense and QboPurchase.
        
        Args:
            expense_id: Database ID of Expense record
            qbo_purchase_id: Database ID of QboPurchase record
        
        Returns:
            PurchaseExpense: The created mapping record
        
        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints
        existing_by_expense = self.mapping_repo.read_by_expense_id(expense_id)
        if existing_by_expense:
            raise ValueError(
                f"Expense {expense_id} is already mapped to QboPurchase {existing_by_expense.qbo_purchase_id}"
            )
        
        existing_by_qbo_purchase = self.mapping_repo.read_by_qbo_purchase_id(qbo_purchase_id)
        if existing_by_qbo_purchase:
            raise ValueError(
                f"QboPurchase {qbo_purchase_id} is already mapped to Expense {existing_by_qbo_purchase.expense_id}"
            )
        
        # Create mapping
        return self.mapping_repo.create(expense_id=expense_id, qbo_purchase_id=qbo_purchase_id)

    def get_mapping_by_expense_id(self, expense_id: int) -> Optional[PurchaseExpense]:
        """
        Get mapping by Expense ID.
        """
        return self.mapping_repo.read_by_expense_id(expense_id)

    def get_mapping_by_qbo_purchase_id(self, qbo_purchase_id: int) -> Optional[PurchaseExpense]:
        """
        Get mapping by QboPurchase ID.
        """
        return self.mapping_repo.read_by_qbo_purchase_id(qbo_purchase_id)

    def sync_to_qbo_purchase(self, expense: Expense, realm_id: str) -> QboPurchase:
        """
        Push local Expense changes back to QBO as a Purchase update.
        Converts AccountBasedExpenseLineDetail → ItemBasedExpenseLineDetail.
        
        Args:
            expense: Local Expense record to sync
            realm_id: QBO realm ID for API access
        
        Returns:
            QboPurchase: The updated local QboPurchase record
            
        Raises:
            ValueError: If no mapping exists, QBO record not found, or no valid line items
        """
        expense_id = int(expense.id) if isinstance(expense.id, str) else expense.id
        
        # 1. Get existing mapping (REQUIRED - must already exist from pull)
        mapping = self.mapping_repo.read_by_expense_id(expense_id)
        if not mapping:
            raise ValueError(
                f"No QBO mapping found for Expense {expense_id}. "
                "Cannot push - expense must originate from QBO sync."
            )
        
        # 2. Get local QboPurchase record (has qbo_id, sync_token, payment_type, account_ref)
        local_qbo_purchase = self.qbo_purchase_repo.read_by_id(mapping.qbo_purchase_id)
        if not local_qbo_purchase:
            raise ValueError(f"QboPurchase {mapping.qbo_purchase_id} not found")
        
        # 3. Get expense line items and build QBO lines
        expense_line_item_service = ExpenseLineItemService()
        expense_line_items = expense_line_item_service.read_by_expense_id(expense_id=expense_id)
        
        qbo_lines = []
        skipped_lines = []
        line_num_to_expense_line_item_id: Dict[int, int] = {}
        for idx, line_item in enumerate(expense_line_items, start=1):
            qbo_line = self._build_qbo_line(line_item, idx)
            if qbo_line:
                qbo_lines.append(qbo_line)
                line_num_to_expense_line_item_id[idx] = int(line_item.id)
            else:
                skipped_lines.append(line_item.id)
        
        # QBO requires at least one line item
        if not qbo_lines:
            if expense_line_items:
                raise ValueError(
                    f"Expense has {len(expense_line_items)} line item(s) but none have QBO Item mappings. "
                    f"SubCostCodes must be mapped to QBO Items first. Skipped line item IDs: {skipped_lines}"
                )
            else:
                raise ValueError("Expense has no line items. QBO requires at least one line item.")
        
        # 4. Build QboPurchaseUpdate (preserving PaymentType, AccountRef from original)
        from integrations.intuit.qbo.purchase.external.schemas import (
            QboPurchaseUpdate,
            QboReferenceType,
        )
        
        qbo_purchase_update = QboPurchaseUpdate(
            id=local_qbo_purchase.qbo_id,
            sync_token=local_qbo_purchase.sync_token,
            payment_type=local_qbo_purchase.payment_type,
            account_ref=QboReferenceType(
                value=local_qbo_purchase.account_ref_value,
                name=local_qbo_purchase.account_ref_name
            ) if local_qbo_purchase.account_ref_value else None,
            entity_ref=QboReferenceType(
                value=local_qbo_purchase.entity_ref_value,
                name=local_qbo_purchase.entity_ref_name
            ) if local_qbo_purchase.entity_ref_value else None,
            txn_date=local_qbo_purchase.txn_date,
            doc_number=local_qbo_purchase.doc_number,
            private_note=expense.memo,
            line=qbo_lines,
            currency_ref=QboReferenceType(
                value=local_qbo_purchase.currency_ref_value,
                name=local_qbo_purchase.currency_ref_name
            ) if local_qbo_purchase.currency_ref_value else None,
            department_ref=QboReferenceType(
                value=local_qbo_purchase.department_ref_value,
                name=local_qbo_purchase.department_ref_name
            ) if local_qbo_purchase.department_ref_value else None,
        )
        
        # 5. Update purchase in QBO. QboHttpClient (via QboPurchaseClient) resolves
        # and refreshes the access token lazily, so no upfront auth call is needed.
        from integrations.intuit.qbo.purchase.external.client import QboPurchaseClient

        logger.info(f"Updating Purchase in QBO for local Expense {expense_id}: qbo_id={local_qbo_purchase.qbo_id}")

        with QboPurchaseClient(realm_id=realm_id) as client:
            updated_purchase = client.update_purchase(qbo_purchase_update)
        
        logger.info(f"Updated QBO Purchase {updated_purchase.id} with new SyncToken {updated_purchase.sync_token}")
        
        # 6. Update local QboPurchase record with new sync_token and line items
        updated_local = self.qbo_purchase_repo.update_by_qbo_id(
            qbo_id=local_qbo_purchase.qbo_id,
            row_version=local_qbo_purchase.row_version_bytes,
            sync_token=updated_purchase.sync_token,
            realm_id=realm_id,
            payment_type=local_qbo_purchase.payment_type,
            account_ref_value=local_qbo_purchase.account_ref_value,
            account_ref_name=local_qbo_purchase.account_ref_name,
            entity_ref_value=local_qbo_purchase.entity_ref_value,
            entity_ref_name=local_qbo_purchase.entity_ref_name,
            credit=local_qbo_purchase.credit,
            txn_date=local_qbo_purchase.txn_date,
            doc_number=local_qbo_purchase.doc_number,
            private_note=expense.memo,
            total_amt=updated_purchase.total_amt,
            currency_ref_value=local_qbo_purchase.currency_ref_value,
            currency_ref_name=local_qbo_purchase.currency_ref_name,
            exchange_rate=local_qbo_purchase.exchange_rate,
            department_ref_value=local_qbo_purchase.department_ref_value,
            department_ref_name=local_qbo_purchase.department_ref_name,
            global_tax_calculation=local_qbo_purchase.global_tax_calculation,
        )
        
        # 7. Update local QboPurchaseLine records
        if updated_purchase.line:
            self._update_local_purchase_lines(
                updated_local.id,
                updated_purchase.line,
                line_num_to_expense_line_item_id=line_num_to_expense_line_item_id,
            )

        return updated_local

    def _build_qbo_line(self, line_item, line_num: int):
        """
        Build a QBO Purchase line from a local ExpenseLineItem.
        Converts to ItemBasedExpenseLineDetail with ItemRef and CustomerRef.
        
        Args:
            line_item: ExpenseLineItem record
            line_num: Line number
            
        Returns:
            QboPurchaseLine or None if no Item mapping exists
        """
        from integrations.intuit.qbo.purchase.external.schemas import (
            QboPurchaseLine,
            QboReferenceType,
            QboItemBasedExpenseLineDetail,
        )
        from decimal import Decimal
        
        # Get QBO ItemRef from SubCostCode
        item_ref = self._get_qbo_item_ref(line_item.sub_cost_code_id)
        if not item_ref:
            logger.warning(f"No QBO Item mapping for sub_cost_code_id={line_item.sub_cost_code_id}, skipping line {line_item.id}")
            return None
        
        # Get QBO CustomerRef from Project
        customer_ref = self._get_qbo_customer_ref(line_item.project_id) if line_item.project_id else None
        
        # Determine billable status
        billable_status = None
        if line_item.is_billable is True:
            if customer_ref:
                billable_status = "Billable" if not getattr(line_item, 'is_billed', False) else "HasBeenBilled"
            else:
                logger.warning(f"Line item {line_item.id} is billable but no CustomerRef available. Setting to NotBillable.")
                billable_status = "NotBillable"
        elif line_item.is_billable is False:
            billable_status = "NotBillable"
        
        # Calculate amount
        line_amount = line_item.amount
        qty = Decimal(str(line_item.quantity)) if line_item.quantity else None
        unit_price = line_item.rate
        
        if line_amount is None and qty is not None and unit_price is not None:
            line_amount = qty * unit_price
        
        if line_amount is None:
            logger.warning(f"Line item {line_item.id} has no amount, using 0.")
            line_amount = Decimal('0')
        
        # Build ItemBasedExpenseLineDetail
        detail = QboItemBasedExpenseLineDetail(
            item_ref=item_ref,
            customer_ref=customer_ref,
            billable_status=billable_status,
            qty=qty,
            unit_price=unit_price,
        )
        
        return QboPurchaseLine(
            line_num=line_num,
            description=line_item.description,
            amount=line_amount,
            detail_type="ItemBasedExpenseLineDetail",
            item_based_expense_line_detail=detail,
        )

    def recode_purchase_line(
        self,
        *,
        realm_id: str,
        qbo_purchase_qbo_id: str,
        target_qbo_line_id: str,
        sub_cost_code_id: int,
        project_id: Optional[int],
        description: Optional[str],
        expected_sync_token: str,
    ) -> dict:
        """
        Surgically recode one 58999 placeholder Purchase line to ItemBasedExpenseLineDetail.

        Round-trips the raw QBO Purchase JSON; mutates only the target line dict in place.
        Performs no local DB or qbo.* cache writes.
        """
        from integrations.intuit.qbo.base.errors import QboSyncTokenMismatchError
        from integrations.intuit.qbo.purchase.connector.expense.business.errors import (
            PurchaseChangedInQboError,
            PurchaseRecodeMappingError,
        )
        from integrations.intuit.qbo.purchase.external.client import QboPurchaseClient

        with QboPurchaseClient(realm_id=realm_id) as client:
            raw = client.get_purchase_raw(qbo_purchase_qbo_id)
            lines = raw.get("Line") or []
            live_sync_token = str(raw.get("SyncToken"))

            target = next(
                (line for line in lines if str(line.get("Id")) == str(target_qbo_line_id)),
                None,
            )
            if target is None:
                return {"status": "line_not_found", "sync_token": live_sync_token}

            # If the line already left 58999, it was recoded by someone (or a prior
            # run). Idempotent success only when it carries OUR intended item;
            # any other coding is a foreign edit -> fail closed to re-review.
            if not self._raw_line_is_categorize_placeholder(target):
                existing_ref = self._get_qbo_item_ref(sub_cost_code_id)
                if (
                    target.get("DetailType") == "ItemBasedExpenseLineDetail"
                    and existing_ref is not None
                    and (target.get("ItemBasedExpenseLineDetail") or {}).get("ItemRef", {}).get("value")
                    == existing_ref.value
                ):
                    return {"status": "already_recoded", "sync_token": live_sync_token}
                raise PurchaseChangedInQboError(
                    qbo_purchase_qbo_id=qbo_purchase_qbo_id,
                    expected_sync_token=str(expected_sync_token),
                    actual_sync_token=live_sync_token,
                )

            # Still on the placeholder: any drift from our snapshot token means the
            # Purchase changed in QBO since queueing -> fail closed.
            if live_sync_token != str(expected_sync_token):
                raise PurchaseChangedInQboError(
                    qbo_purchase_qbo_id=qbo_purchase_qbo_id,
                    expected_sync_token=str(expected_sync_token),
                    actual_sync_token=live_sync_token,
                )

            item_ref = self._get_qbo_item_ref(sub_cost_code_id)
            if item_ref is None:
                raise PurchaseRecodeMappingError(sub_cost_code_id=sub_cost_code_id)

            customer_ref = self._get_qbo_customer_ref(project_id) if project_id else None

            old = target.get("AccountBasedExpenseLineDetail") or {}
            item_detail: dict = {
                "ItemRef": {"value": item_ref.value, "name": item_ref.name},
            }
            if customer_ref is not None:
                item_detail["CustomerRef"] = {
                    "value": customer_ref.value,
                    "name": customer_ref.name,
                }
            elif old.get("CustomerRef"):
                item_detail["CustomerRef"] = old["CustomerRef"]
            for carry_key in ("ClassRef", "BillableStatus", "TaxCodeRef", "MarkupInfo"):
                if old.get(carry_key) is not None:
                    item_detail[carry_key] = old[carry_key]

            target["DetailType"] = "ItemBasedExpenseLineDetail"
            target["ItemBasedExpenseLineDetail"] = item_detail
            target.pop("AccountBasedExpenseLineDetail", None)
            if description is not None:
                target["Description"] = description

            # Strip QBO response-only fields before echoing the document back on
            # a full update — MetaData is server-owned, and domain/sparse are read
            # markers. QBO recomputes MetaData; leaving them in risks rejection.
            for _read_only in ("MetaData", "domain", "sparse"):
                raw.pop(_read_only, None)

            try:
                updated = client.update_purchase_raw(raw)
            except QboSyncTokenMismatchError as exc:
                raise PurchaseChangedInQboError(
                    qbo_purchase_qbo_id=qbo_purchase_qbo_id,
                    expected_sync_token=str(expected_sync_token),
                    actual_sync_token="unknown",
                ) from exc

        return {
            "status": "written",
            "sync_token": str(updated.get("SyncToken")),
            "qbo_purchase_qbo_id": qbo_purchase_qbo_id,
            "target_qbo_line_id": target_qbo_line_id,
        }

    @staticmethod
    def _raw_line_is_categorize_placeholder(line_dict: dict) -> bool:
        """True when the line sits on the 58999 NEED TO CATEGORIZE placeholder account."""
        detail = line_dict.get("AccountBasedExpenseLineDetail")
        if not detail:
            return False
        account_ref = detail.get("AccountRef") or {}
        name = account_ref.get("name")
        if not name:
            return False
        return "need to categorize" in name.lower()

    def _get_qbo_item_ref(self, sub_cost_code_id: int):
        """
        Get QBO ItemRef from local sub_cost_code_id.
        
        Args:
            sub_cost_code_id: Local SubCostCode database ID
            
        Returns:
            QboReferenceType with QBO item value and name, or None
        """
        from integrations.intuit.qbo.purchase.external.schemas import QboReferenceType
        from integrations.intuit.qbo.item.connector.sub_cost_code.persistence.repo import ItemSubCostCodeRepository
        from integrations.intuit.qbo.item.persistence.repo import QboItemRepository
        
        if not sub_cost_code_id:
            logger.debug("_get_qbo_item_ref called with None sub_cost_code_id")
            return None
        
        item_sub_cost_code_repo = ItemSubCostCodeRepository()
        qbo_item_repo = QboItemRepository()
        
        # Find ItemSubCostCode mapping
        item_mapping = item_sub_cost_code_repo.read_by_sub_cost_code_id(sub_cost_code_id)
        if not item_mapping:
            logger.warning(f"ItemSubCostCode mapping not found for sub_cost_code_id: {sub_cost_code_id}")
            return None
        
        # Get QboItem
        qbo_item = qbo_item_repo.read_by_id(item_mapping.qbo_item_id)
        if not qbo_item or not qbo_item.qbo_id:
            logger.debug(f"QboItem not found for qbo_item_id: {item_mapping.qbo_item_id}")
            return None
        
        return QboReferenceType(value=qbo_item.qbo_id, name=qbo_item.name)

    def _get_qbo_customer_ref(self, project_id: int):
        """
        Get QBO CustomerRef from local project_id.
        
        Args:
            project_id: Local Project database ID
            
        Returns:
            QboReferenceType with QBO customer value and name, or None
        """
        from integrations.intuit.qbo.purchase.external.schemas import QboReferenceType
        from integrations.intuit.qbo.customer.connector.project.persistence.repo import CustomerProjectRepository
        from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository
        
        if not project_id:
            return None
        
        customer_project_repo = CustomerProjectRepository()
        qbo_customer_repo = QboCustomerRepository()
        
        # Find CustomerProject mapping
        customer_mapping = customer_project_repo.read_by_project_id(project_id)
        if not customer_mapping:
            logger.debug(f"CustomerProject mapping not found for project_id: {project_id}")
            return None
        
        # Get QboCustomer
        qbo_customer = qbo_customer_repo.read_by_id(customer_mapping.qbo_customer_id)
        if not qbo_customer or not qbo_customer.qbo_id:
            logger.debug(f"QboCustomer not found for qbo_customer_id: {customer_mapping.qbo_customer_id}")
            return None
        
        return QboReferenceType(value=qbo_customer.qbo_id, name=qbo_customer.display_name)

    def _update_local_purchase_lines(
        self,
        qbo_purchase_id: int,
        qbo_lines: list,
        line_num_to_expense_line_item_id: Optional[Dict[int, int]] = None,
    ) -> None:
        """
        Update local QboPurchaseLine records after QBO update.

        Args:
            qbo_purchase_id: Local QboPurchase database ID
            qbo_lines: List of QboPurchaseLine from API response
            line_num_to_expense_line_item_id: Optional map of line_num -> ExpenseLineItem.id.
                When provided, a PurchaseLineExpenseLineItem mapping is created for each
                stored QboPurchaseLine that does not already have one. Without this,
                subsequent sync_from_qbo runs would duplicate the ExpenseLineItems.
        """
        for qbo_line in qbo_lines:
            try:
                # Check if line exists
                existing_line = None
                if qbo_line.id:
                    existing_line = self.qbo_purchase_line_repo.read_by_qbo_purchase_id_and_qbo_line_id(
                        qbo_purchase_id=qbo_purchase_id,
                        qbo_line_id=qbo_line.id
                    )

                # Extract references from line detail
                item_ref_value = None
                item_ref_name = None
                customer_ref_value = None
                customer_ref_name = None
                billable_status = None
                qty = None
                unit_price = None

                if qbo_line.item_based_expense_line_detail:
                    detail = qbo_line.item_based_expense_line_detail
                    if detail.item_ref:
                        item_ref_value = detail.item_ref.value
                        item_ref_name = detail.item_ref.name
                    if detail.customer_ref:
                        customer_ref_value = detail.customer_ref.value
                        customer_ref_name = detail.customer_ref.name
                    billable_status = detail.billable_status
                    qty = detail.qty
                    unit_price = detail.unit_price

                stored_line = None
                if existing_line:
                    stored_line = self.qbo_purchase_line_repo.update_by_id(
                        id=existing_line.id,
                        row_version=existing_line.row_version_bytes,
                        line_num=qbo_line.line_num,
                        description=qbo_line.description,
                        amount=qbo_line.amount,
                        detail_type=qbo_line.detail_type,
                        item_ref_value=item_ref_value,
                        item_ref_name=item_ref_name,
                        account_ref_value=None,  # Cleared - now using ItemBasedExpenseLineDetail
                        account_ref_name=None,
                        customer_ref_value=customer_ref_value,
                        customer_ref_name=customer_ref_name,
                        class_ref_value=None,
                        class_ref_name=None,
                        billable_status=billable_status,
                        qty=qty,
                        unit_price=unit_price,
                        markup_percent=None,
                    ) or existing_line
                else:
                    stored_line = self.qbo_purchase_line_repo.create(
                        qbo_purchase_id=qbo_purchase_id,
                        qbo_line_id=qbo_line.id,
                        line_num=qbo_line.line_num,
                        description=qbo_line.description,
                        amount=qbo_line.amount,
                        detail_type=qbo_line.detail_type,
                        item_ref_value=item_ref_value,
                        item_ref_name=item_ref_name,
                        account_ref_value=None,
                        account_ref_name=None,
                        customer_ref_value=customer_ref_value,
                        customer_ref_name=customer_ref_name,
                        class_ref_value=None,
                        class_ref_name=None,
                        billable_status=billable_status,
                        qty=qty,
                        unit_price=unit_price,
                        markup_percent=None,
                    )

                # Create PurchaseLineExpenseLineItem mapping when correlation is available.
                # Without this, subsequent sync_from_qbo creates duplicate ExpenseLineItems
                # because no mapping exists to point back to the local ExpenseLineItem.
                if line_num_to_expense_line_item_id and stored_line and qbo_line.line_num:
                    eli_id = line_num_to_expense_line_item_id.get(qbo_line.line_num)
                    if eli_id is not None:
                        stored_line_id = int(stored_line.id) if isinstance(stored_line.id, str) else stored_line.id
                        existing_mapping = self._line_connector.get_mapping_by_qbo_purchase_line_id(stored_line_id)
                        if existing_mapping is None:
                            try:
                                self._line_connector.create_mapping(
                                    expense_line_item_id=eli_id,
                                    qbo_purchase_line_id=stored_line_id,
                                )
                                logger.info(
                                    f"Created line mapping: ExpenseLineItem {eli_id} <-> QboPurchaseLine {stored_line_id}"
                                )
                            except ValueError as e:
                                logger.warning(f"Could not create line mapping: {e}")
            except Exception as e:
                logger.error(f"Failed to update QboPurchaseLine: {e}")


def sync_purchase_attachments_to_expense_line_items(
    expense_id: int,
    qbo_attachables: list,
) -> int:
    """
    Link QBO attachables (already synced to Attachments) to all ExpenseLineItems for this expense.
    Mirrors Bill _link_attachments_to_bill_line_items: each attachment is linked to each line item.
    Returns count of ExpenseLineItemAttachment links created.
    """
    if not qbo_attachables:
        return 0

    from integrations.intuit.qbo.attachable.connector.attachment.persistence.repo import AttachableAttachmentRepository
    from entities.attachment.business.service import AttachmentService
    from entities.expense_line_item.business.service import ExpenseLineItemService
    from entities.expense_line_item_attachment.business.service import ExpenseLineItemAttachmentService

    expense_line_item_service = ExpenseLineItemService()
    expense_line_item_attachment_service = ExpenseLineItemAttachmentService()
    attachment_service = AttachmentService()
    attachable_attachment_repo = AttachableAttachmentRepository()

    line_items = expense_line_item_service.read_by_expense_id(expense_id=expense_id)
    if not line_items:
        logger.debug(f"No ExpenseLineItems found for Expense {expense_id}")
        return 0

    linked = 0

    # Pre-load existing links once, then track within-run links in the same set —
    # avoids an N+1 re-query (each per-line read also re-resolved public_id->id) on
    # every (attachment x line item) iteration.
    linked_public_ids = {
        a.expense_line_item_public_id
        for a in expense_line_item_attachment_service.read_by_expense_line_item_ids(
            [li.public_id for li in line_items if li.public_id]
        )
    }

    for qbo_attachable in qbo_attachables:
        mapping = attachable_attachment_repo.read_by_qbo_attachable_id(qbo_attachable.id)
        if not mapping:
            logger.debug(f"No Attachment mapping found for QboAttachable {qbo_attachable.id}")
            continue
        attachment = attachment_service.read_by_id(mapping.attachment_id)
        if not attachment or not attachment.public_id:
            continue
        # ExpenseLineItemAttachment is 1:1 — each line item can only hold one attachment.
        # Link this attachment to any line items that are not yet linked; skip those that are.
        attachment_linked_count = 0
        for line_item in line_items:
            if not line_item.public_id or line_item.public_id in linked_public_ids:
                continue
            try:
                expense_line_item_attachment_service.create(
                    expense_line_item_public_id=line_item.public_id,
                    attachment_public_id=attachment.public_id,
                )
                linked += 1
                attachment_linked_count += 1
                linked_public_ids.add(line_item.public_id)
            except Exception as e:
                logger.debug(f"Could not link Attachment {attachment.id} to ExpenseLineItem {line_item.id}: {e}")
        if attachment_linked_count == 0:
            logger.warning(
                f"Expense {expense_id}: Attachment {attachment.id} (QboAttachable {qbo_attachable.id}) "
                f"could not be linked — all {len(line_items)} line item(s) already have an attachment. "
                f"ExpenseLineItemAttachment is 1:1; this attachment is unlinked."
            )

    if linked > 0:
        logger.info(f"Created {linked} ExpenseLineItemAttachment links for Expense {expense_id}")
    return linked
