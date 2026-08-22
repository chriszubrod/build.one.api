# Python Standard Library Imports
import logging
from typing import List, Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.bill.connector.bill.business.model import BillBill
from integrations.intuit.qbo.bill.connector.bill.persistence.repo import BillBillRepository
from integrations.intuit.qbo.bill.business.model import QboBill, QboBillLine
from integrations.intuit.qbo.bill.persistence.repo import QboBillRepository, QboBillLineRepository
from integrations.intuit.qbo.bill.external.client import QboBillClient
from integrations.intuit.qbo.bill.external.schemas import (
    QboBillCreate,
    QboBillLine as QboBillLineSchema,
    QboReferenceType,
    QboItemBasedExpenseLineDetail,
    QboAccountBasedExpenseLineDetail,
)
from integrations.intuit.qbo.vendor.connector.vendor.persistence.repo import VendorVendorRepository
from integrations.intuit.qbo.vendor.persistence.repo import QboVendorRepository
from integrations.intuit.qbo.item.connector.sub_cost_code.persistence.repo import ItemSubCostCodeRepository
from integrations.intuit.qbo.item.persistence.repo import QboItemRepository
from integrations.intuit.qbo.customer.connector.project.persistence.repo import CustomerProjectRepository
from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository
from integrations.intuit.qbo.account.persistence.repo import QboAccountRepository
from integrations.intuit.qbo.account.business.service import AP_ACCOUNT_TYPE, select_ap_account
from integrations.intuit.qbo.term.connector.payment_term.persistence.repo import TermPaymentTermRepository
from integrations.intuit.qbo.term.persistence.repo import QboTermRepository
from entities.bill.business.service import BillService
from entities.bill.business.model import Bill
from entities.bill_line_item.business.service import BillLineItemService
from entities.vendor.business.service import VendorService
from entities.project.business.service import ProjectService
from entities.company.business.service import CompanyService
from entities.payment_term.business.service import PaymentTermService
from integrations.intuit.qbo.base.pull_race import guard_lines_present
from integrations.intuit.qbo.base.compensation import rollback_orphan_header
from integrations.intuit.qbo.base.field_ownership import preserve_human_edited_ref, qbo_ref_or_placeholder
from integrations.intuit.qbo.base.identity_consistency import (
    verify_project_qbo_identity,
    verify_vendor_qbo_identity,
)
from integrations.intuit.qbo.base.identity_fastpath import (
    raise_concurrent_write_race,
    run_identity_fastpath,
)
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.reconciliation_recorder import record_mapping_issue
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from shared.database import DatabaseConstraintError

logger = logging.getLogger(__name__)


class BillBillConnector:
    """
    Connector service for synchronization between QboBill and Bill modules.
    """

    def __init__(
        self,
        mapping_repo: Optional[BillBillRepository] = None,
        bill_service: Optional[BillService] = None,
        vendor_service: Optional[VendorService] = None,
        vendor_vendor_repo: Optional[VendorVendorRepository] = None,
        qbo_vendor_repo: Optional[QboVendorRepository] = None,
        qbo_bill_repo: Optional[QboBillRepository] = None,
        qbo_bill_line_repo: Optional[QboBillLineRepository] = None,
        bill_line_item_service: Optional[BillLineItemService] = None,
        item_sub_cost_code_repo: Optional[ItemSubCostCodeRepository] = None,
        qbo_item_repo: Optional[QboItemRepository] = None,
        customer_project_repo: Optional[CustomerProjectRepository] = None,
        qbo_customer_repo: Optional[QboCustomerRepository] = None,
        project_service: Optional[ProjectService] = None,
        qbo_account_repo: Optional[QboAccountRepository] = None,
        term_payment_term_repo: Optional[TermPaymentTermRepository] = None,
        qbo_term_repo: Optional[QboTermRepository] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
        company_service: Optional[CompanyService] = None,
        payment_term_service: Optional[PaymentTermService] = None,
    ):
        """Initialize the BillBillConnector."""
        self.mapping_repo = mapping_repo or BillBillRepository()
        self.bill_service = bill_service or BillService()
        self.vendor_service = vendor_service or VendorService()
        self.vendor_vendor_repo = vendor_vendor_repo or VendorVendorRepository()
        self.qbo_vendor_repo = qbo_vendor_repo or QboVendorRepository()
        self.qbo_bill_repo = qbo_bill_repo or QboBillRepository()
        self.qbo_bill_line_repo = qbo_bill_line_repo or QboBillLineRepository()
        self.bill_line_item_service = bill_line_item_service or BillLineItemService()
        self.item_sub_cost_code_repo = item_sub_cost_code_repo or ItemSubCostCodeRepository()
        self.qbo_item_repo = qbo_item_repo or QboItemRepository()
        self.customer_project_repo = customer_project_repo or CustomerProjectRepository()
        self.qbo_customer_repo = qbo_customer_repo or QboCustomerRepository()
        self.project_service = project_service or ProjectService()
        self.qbo_account_repo = qbo_account_repo or QboAccountRepository()
        self.term_payment_term_repo = term_payment_term_repo or TermPaymentTermRepository()
        self.qbo_term_repo = qbo_term_repo or QboTermRepository()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()
        self.company_service = company_service or CompanyService()
        self.payment_term_service = payment_term_service or PaymentTermService()

    def sync_from_qbo_bill(self, qbo_bill: QboBill, qbo_bill_lines: List[QboBillLine]) -> Bill:
        """
        Sync data from QboBill to Bill module.
        
        This method:
        1. Checks if a mapping exists
        2. Creates or updates the Bill accordingly
        3. Syncs line items to BillLineItem module
        
        Args:
            qbo_bill: QboBill record
            qbo_bill_lines: List of QboBillLine records for this bill
        
        Returns:
            Bill: The synced Bill record
        """
        # Find vendor mapping to get Vendor public_id
        vendor_public_id = self._get_vendor_public_id(qbo_bill.vendor_ref_value, qbo_bill.realm_id)
        if not vendor_public_id:
            raise ValueError(f"No vendor mapping found for QBO vendor ref: {qbo_bill.vendor_ref_value}")
        
        # Map QBO Bill fields to Bill module fields
        bill_number = qbo_ref_or_placeholder(qbo_bill.doc_number, qbo_bill.qbo_id)
        bill_date = qbo_bill.txn_date or ""
        due_date = qbo_bill.due_date or ""
        memo = qbo_bill.private_note
        total_amount = qbo_bill.total_amt

        # Last-resort guard against the QBO pull-race that mints half-built bills (see
        # base.pull_race). Pull scripts pre-read past the race; this protects every other caller.
        guard_lines_present(
            qbo_bill_lines, total_amount,
            entity_label="QboBill", entity_id=qbo_bill.id, qbo_id=qbo_bill.qbo_id,
        )

        def _apply_bill_fields(direct: Bill) -> Bill:
            """
            Write the QBO-derived fields onto an existing Bill, stamp identity, then
            sync its line items. Shared by the fast path's apply_fields and the
            legacy "mapping found" branch below so the QboBill->Bill field mapping
            lives in exactly one place (no drift between the two update sites) —
            mirrors PurchaseExpenseConnector's `_apply_expense_fields`.

            U-027 (rule of three): never clobber a human-corrected bill_number on
            re-pull. Preserve the stored value unless it is empty/null or the
            QBO-<id> placeholder (which still upgrades to a real doc_number). CREATE
            path below is unchanged. See base.field_ownership.
            """
            effective_bill_number = preserve_human_edited_ref(
                direct.bill_number, bill_number, qbo_bill.qbo_id
            )
            updated = self.bill_service.update_by_public_id(
                direct.public_id,
                vendor_public_id=vendor_public_id,
                bill_date=bill_date,
                due_date=due_date,
                bill_number=effective_bill_number,
                total_amount=total_amount,
                memo=memo,
                is_draft=False,
                row_version=direct.row_version,
            )
            if updated is None:
                # ROWVERSION race: a concurrent writer touched this exact Bill
                # between the read and this UPDATE, so it affected 0 rows. Shared
                # by both the fast path and the legacy "mapping found" branch
                # below (line ~269), so both are fixed by this one guard (U-291).
                logger.error(
                    f"Failed to update Bill {direct.id} from QboBill {qbo_bill.id} - "
                    f"update_by_public_id returned None (concurrent write race)"
                )
                raise_concurrent_write_race(entity_label="Bill", entity_id=direct.id)
            bill_id = coerce_id(updated.id)
            # Bill/Expense carry SyncToken as part of their identity (unlike
            # Project/Company/BillCredit) — this re-stamp is NOT redundant even when
            # QboId/RealmId are already correct-by-construction (the fast path only
            # found `direct` because they already match): it refreshes SyncToken on
            # every pull, which the legacy path also always did.
            self.bill_service.repo.set_qbo_identity(
                id=bill_id,
                qbo_id=qbo_bill.qbo_id,
                realm_id=qbo_bill.realm_id,
                sync_token=getattr(qbo_bill, "sync_token", None),
            )
            self._sync_line_items(bill_id, qbo_bill_lines, qbo_bill.realm_id)
            return updated

        # U-283 (Phase-4): resolve identity directly against dbo.Bill's native
        # QboId/RealmId (U-238a) before falling back to the qbo.BillBill
        # mapping-table hop below. Every Bill synced even once already carries
        # this identity (set_qbo_identity is called on both the update and
        # create paths), so this covers the steady-state case without touching
        # qbo.Bill at all. Mirrors CompanyInfoCompanyConnector's U-287 fast path
        # exactly — conflict->RAISE is structural (base.identity_fastpath), never
        # a fall-through to the legacy path below.
        outcome = run_identity_fastpath(
            qbo_id=qbo_bill.qbo_id,
            realm_id=qbo_bill.realm_id,
            external_id=qbo_bill.id,
            entity_label="Bill",
            external_label="QboBill",
            mapping_label="BillBill",
            read_direct_by_qbo_identity=self.bill_service.read_by_qbo_identity,
            read_by_local_id=self.mapping_repo.read_by_bill_id,
            read_by_external_id=self.mapping_repo.read_by_qbo_bill_id,
            external_id_attr="qbo_bill_id",
            record_conflict_issue=lambda entity, by_local, by_external: (
                self._raise_identity_mapping_conflict_issue(
                    qbo_bill=qbo_bill,
                    dbo_bill_id=coerce_id(entity.id),
                    local_side_mapping=by_local,
                    qbo_side_mapping=by_external,
                )
            ),
            conflict_message=lambda entity: (
                f"BillBill identity conflict for QboBill {qbo_bill.qbo_id} "
                f"(id={qbo_bill.id}): dbo.Bill {entity.id} already carries this "
                f"identity but the mapping table disagrees. Not auto-repointed; "
                f"see the recorded reconciliation issue. Skipping until a human "
                f"resolves it."
            ),
            create_mapping=lambda local_id: self.mapping_repo.create(
                bill_id=local_id, qbo_bill_id=qbo_bill.id
            ),
            apply_fields=_apply_bill_fields,
        )
        if outcome.hit:
            return outcome.entity

        # Check for existing mapping
        mapping = self.mapping_repo.read_by_qbo_bill_id(qbo_bill.id)

        if mapping:
            # Found existing mapping. Resolve the Bill to update. HEAL-don't-delete
            # (U-031, mirroring U-029 Purchase->Expense): a transient empty-read must
            # NEVER delete the mapping and fall through to CREATE — that would mint a
            # DUPLICATE Bill (the exact hazard U-029 fixed for Expense).
            bill = self.bill_service.read_by_id(mapping.bill_id)
            if bill:
                logger.info(f"Updating existing Bill {bill.id} from QboBill {qbo_bill.id}")
            else:
                # Bound Bill read empty. Bill has no unique NAME like Project, and there
                # is no mapping-repoint sproc, so re-resolve by the closest natural
                # fingerprint — (bill_number, vendor) — and heal ONLY when it re-binds the
                # SAME Bill the mapping already targets (a confirmed transient empty-read).
                # The fingerprint keys on the QBO-derived bill_number (what CREATE writes);
                # the same-id gate makes a wrong/duplicate row safe under a non-TOP-1
                # fingerprint proc (id != mapping.bill_id → record+raise, never a wrong
                # bind). See _record_missing_bill_issue.
                replacement = self.bill_service.read_by_bill_number_and_vendor_public_id(
                    bill_number, vendor_public_id
                )
                if replacement and replacement.id == mapping.bill_id:
                    logger.warning(
                        f"Bill {mapping.bill_id} read empty for QboBill {qbo_bill.id} but "
                        f"re-resolved by (bill_number, vendor) — transient empty-read; "
                        f"healing in place, not recreating."
                    )
                    bill = replacement
                else:
                    # No fingerprint match, or a match under a DIFFERENT id we cannot
                    # safely repoint to (no mapping-update sproc): preserve the mapping,
                    # create nothing, record a critical reconciliation issue, and RAISE.
                    # The pull caller treats this ValueError as a per-item skip (watermark
                    # advances, sync stays healthy); the issue is the durable follow-up.
                    self._record_missing_bill_issue(
                        qbo_bill=qbo_bill, mapping=mapping, fingerprint=replacement
                    )
                    raise ValueError(
                        f"BillBill mapping {mapping.id} points at missing Bill "
                        f"{mapping.bill_id} and no local Bill fingerprinted by bill_number "
                        f"'{bill_number}' + vendor resolves to it for QboBill "
                        f"{qbo_bill.id}; preserving mapping, skipping."
                    )

            bill = _apply_bill_fields(bill)
            return bill

        # Create new Bill
        logger.info(f"Creating new Bill from QboBill {qbo_bill.id}: bill_number={bill_number}")
        bill = self.bill_service.create(
            vendor_public_id=vendor_public_id,
            bill_date=bill_date,
            due_date=due_date,
            bill_number=bill_number,
            total_amount=total_amount,
            memo=memo,
            is_draft=False,
            # QBO-origin bills have no local PDF; the universal attachment rule
            # does not apply. Line items are created by _sync_line_items below,
            # not by create()'s placeholder-attachment path.
            require_attachment=False,
        )
        
        # Create mapping
        bill_id = coerce_id(bill.id)
        try:
            mapping = self.create_mapping(
                bill_id=bill_id,
                qbo_bill_id=qbo_bill.id,
                qbo_id=qbo_bill.qbo_id,
                realm_id=qbo_bill.realm_id,
                sync_token=getattr(qbo_bill, "sync_token", None),
            )
            logger.info(f"Created mapping: Bill {bill_id} <-> QboBill {qbo_bill.id}")
        except (ValueError, DatabaseConstraintError) as e:
            logger.warning(f"Could not create mapping: {e}")
        
        # Compensating rollback — a permanent line failure must not leave a header-only zombie;
        # delete the just-created header + qbo.BillBill mapping and re-raise (watermark holds;
        # re-pull is idempotent).
        try:
            self._sync_line_items(bill_id, qbo_bill_lines, qbo_bill.realm_id)
        except Exception:
            def _delete_bill_mapping():
                _m = self.mapping_repo.read_by_bill_id(bill_id)
                if _m:
                    self.mapping_repo.delete_by_id(_m.id)
            rollback_orphan_header(
                delete_header=lambda: self.bill_service.delete_by_public_id(bill.public_id),
                delete_mapping=_delete_bill_mapping,
                entity_label='Bill', entity_id=bill_id,
            )
            raise
        
        return bill

    def _record_missing_bill_issue(
        self,
        *,
        qbo_bill: QboBill,
        mapping: BillBill,
        fingerprint: Optional[Bill] = None,
    ) -> None:
        """
        Record an orphaned-mapping detection on qbo.ReconciliationIssue, failure-
        isolated: a failed insert is logged loud but never breaks the sync (mirrors
        the Purchase/CustomerProject connectors' recorders).

        Triggered when a BillBill mapping exists but its bound Bill read empty AND the
        (bill_number, vendor) fingerprint did not re-resolve to that same Bill. We
        deliberately do NOT delete the mapping or create a Bill here — a transient
        empty-read would otherwise mint a duplicate; the mapping is preserved for the
        next tick / a human to resolve.
        """
        if fingerprint is not None:
            fingerprint_note = (
                f" A different Bill {fingerprint.id} matches the (bill_number, vendor) "
                f"fingerprint but is not the mapped row; not repointing (no "
                f"mapping-update path)."
            )
        else:
            fingerprint_note = (
                " No local Bill matches the (bill_number, vendor) fingerprint."
            )
        details = (
            f"Orphaned BillBill mapping. Mapping {mapping.id} (QboBill {qbo_bill.id}, "
            f"QboId={qbo_bill.qbo_id}) points at Bill {mapping.bill_id} which no longer "
            f"reads.{fingerprint_note} Mapping preserved; no Bill created. Investigate "
            f"whether the Bill was deleted/renumbered."
        )
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="orphaned_bill_bill_mapping",
            entity_type="Bill",
            entity_public_id=None,
            qbo_id=str(qbo_bill.qbo_id) if qbo_bill.qbo_id else None,
            realm_id=qbo_bill.realm_id or "",
            details=details,
        )

    def _raise_identity_mapping_conflict_issue(
        self,
        *,
        qbo_bill: QboBill,
        dbo_bill_id: int,
        local_side_mapping: Optional[BillBill],
        qbo_side_mapping: Optional[BillBill],
    ) -> None:
        """
        Record a dbo-identity <-> mapping-table split found by run_identity_fastpath's
        resolve_mapping_state. Mirrors CompanyInfoCompanyConnector's identically named/
        shaped method — covers all three conflict shapes (qbo-side only, local-side
        only, or both) in ONE issue, never silently dropping either side's blocker.
        """
        parts = [
            f"BillBill identity conflict. dbo.Bill {dbo_bill_id} carries native QBO "
            f"identity for QboBill {qbo_bill.id} (QboId={qbo_bill.qbo_id}, "
            f"RealmId={qbo_bill.realm_id})."
        ]
        if qbo_side_mapping:
            parts.append(
                f"qbo-side: the mapping table still binds that same QboBill to a "
                f"DIFFERENT Bill {qbo_side_mapping.bill_id} (mapping {qbo_side_mapping.id})."
            )
        if local_side_mapping:
            parts.append(
                f"local-side: Bill {dbo_bill_id}'s own mapping row (mapping "
                f"{local_side_mapping.id}) still binds it to a DIFFERENT QboBill "
                f"{local_side_mapping.qbo_bill_id}."
            )
        parts.append("Not auto-repointed — investigate which side is correct.")
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="bill_identity_conflict",
            entity_type="Bill",
            entity_public_id=None,
            qbo_id=str(qbo_bill.qbo_id) if qbo_bill.qbo_id else None,
            realm_id=qbo_bill.realm_id or "",
            details=" ".join(parts),
        )

    # One of FIVE near-identical dbo-first/legacy-fallback vendor-ref resolvers
    # (U-284v): this one, this file's own _get_qbo_vendor_ref (push),
    # PurchaseExpenseConnector._get_vendor_public_id,
    # VendorCreditBillCreditConnector._get_vendor_public_id,
    # ExpenseCodingItemService._resolve_vendor_id. Hand-copied deliberately,
    # mirroring _get_project_public_id's own precedent — see TODO.md's
    # U-005[reuse] entry before adding a 6th copy or consolidating.
    def _get_vendor_public_id(self, qbo_vendor_ref_value: str, realm_id: Optional[str] = None) -> Optional[str]:
        """
        Get the Vendor public_id from QBO vendor reference value.

        Args:
            qbo_vendor_ref_value: QBO vendor reference value (QBO Vendor ID)
            realm_id: Optional QBO realm ID for realm-scoped direct lookup

        Returns:
            str: Vendor public_id or None
        """
        if not qbo_vendor_ref_value:
            return None

        # U-284v: try dbo.Vendor's native QboId/RealmId directly first (mirrors
        # U-283/U-283b's _get_project_public_id pattern) before falling back to
        # the qbo.QboVendor -> qbo.VendorVendor hop below. Read-only resolver —
        # a disagreement just falls through to the legacy hop, no hard stop
        # (nothing is written here to protect).
        direct_vendor = self.vendor_service.read_by_qbo_identity(qbo_vendor_ref_value, realm_id)
        if direct_vendor:
            verified_qbo_id = verify_vendor_qbo_identity(
                direct_vendor,
                vendor_vendor_repo=self.vendor_vendor_repo,
                qbo_vendor_repo=self.qbo_vendor_repo,
            )
            if verified_qbo_id:
                return direct_vendor.public_id

        # First find the QboVendor by qbo_id
        qbo_vendor = self.qbo_vendor_repo.read_by_qbo_id(qbo_vendor_ref_value)
        if not qbo_vendor:
            logger.warning(f"QboVendor not found for qbo_id: {qbo_vendor_ref_value}")
            return None
        
        # Then find the VendorVendor mapping
        vendor_mapping = self.vendor_vendor_repo.read_by_qbo_vendor_id(qbo_vendor.id)
        if not vendor_mapping:
            logger.warning(f"VendorVendor mapping not found for QboVendor ID: {qbo_vendor.id}")
            return None
        
        # Get the Vendor
        vendor = self.vendor_service.read_by_id(vendor_mapping.vendor_id)
        if not vendor:
            logger.warning(f"Vendor not found for ID: {vendor_mapping.vendor_id}")
            return None
        
        return vendor.public_id

    def _sync_line_items(self, bill_id: int, qbo_bill_lines: List[QboBillLine], realm_id: Optional[str] = None) -> None:
        """
        Sync bill line items to BillLineItem module.
        
        Args:
            bill_id: Database ID of the Bill
            qbo_bill_lines: List of QboBillLine records
        """
        if not qbo_bill_lines:
            return
        
        # Import here to avoid circular dependencies
        from integrations.intuit.qbo.bill.connector.bill_line_item.business.service import BillLineItemConnector
        
        line_connector = BillLineItemConnector()

        # Attempt EVERY line (so the log enumerates all problems in one pass), collect
        # failures, then RAISE if any failed. The Bill header total was already written
        # from QBO, so swallowing a per-line failure would leave a silently unbalanced
        # bill (header total != sum of lines). Raising marks the whole bill failed so
        # the pull watermark holds and it retries; re-pull is idempotent.
        failed = []
        for qbo_line in qbo_bill_lines:
            try:
                line_connector.sync_from_qbo_bill_line(bill_id, qbo_line, realm_id)
            except Exception as e:
                logger.error(f"Failed to sync QboBillLine {qbo_line.id} to BillLineItem: {e}")
                failed.append((qbo_line.id, str(e)))
        if failed:
            raise RuntimeError(
                f"{len(failed)} of {len(qbo_bill_lines)} bill line(s) failed to project for "
                f"bill_id={bill_id}: {failed}"
            )

    def create_mapping(
        self,
        bill_id: int,
        qbo_bill_id: int,
        *,
        qbo_id: Optional[str],
        realm_id: Optional[str],
        sync_token: Optional[str] = None,
    ) -> BillBill:
        """
        Create a mapping between Bill and QboBill.
        
        Args:
            bill_id: Database ID of Bill record
            qbo_bill_id: Database ID of QboBill record
        
        Returns:
            BillBill: The created mapping record
        
        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints
        existing_by_bill = self.mapping_repo.read_by_bill_id(bill_id)
        if existing_by_bill:
            raise ValueError(
                f"Bill {bill_id} is already mapped to QboBill {existing_by_bill.qbo_bill_id}"
            )
        
        existing_by_qbo_bill = self.mapping_repo.read_by_qbo_bill_id(qbo_bill_id)
        if existing_by_qbo_bill:
            raise ValueError(
                f"QboBill {qbo_bill_id} is already mapped to Bill {existing_by_qbo_bill.bill_id}"
            )
        
        # Stamp dbo-native identity FIRST — if this fails, nothing else has been
        # created yet, so the caller's existing rollback (delete the just-created
        # entity) fully cleans up with no orphaned mapping row.
        self.bill_service.repo.set_qbo_identity(
            id=bill_id,
            qbo_id=qbo_id,
            realm_id=realm_id,
            sync_token=sync_token,
        )
        mapping = self.mapping_repo.create(bill_id=bill_id, qbo_bill_id=qbo_bill_id)
        return mapping

    def get_mapping_by_bill_id(self, bill_id: int) -> Optional[BillBill]:
        """
        Get mapping by Bill ID.
        """
        return self.mapping_repo.read_by_bill_id(bill_id)

    def get_mapping_by_qbo_bill_id(self, qbo_bill_id: int) -> Optional[BillBill]:
        """
        Get mapping by QboBill ID.
        """
        return self.mapping_repo.read_by_qbo_bill_id(qbo_bill_id)

    def sync_to_qbo_bill(self, bill: Bill, realm_id: str) -> QboBill:
        """
        Sync a local Bill to QuickBooks Online.
        
        This method:
        1. Checks if a mapping already exists (skip if already synced)
        2. Looks up vendor mapping to get QBO vendor reference
        3. Builds QBO Bill payload with line items
        4. Creates Bill in QBO via API
        5. Stores QboBill locally and creates mapping
        
        Args:
            bill: Local Bill record to sync
            realm_id: QBO realm ID for API access
        
        Returns:
            QboBill: The local QboBill record created
            
        Raises:
            ValueError: If mapping lookup fails (vendor not mapped, etc.)
        """
        bill_id = coerce_id(bill.id)
        
        # Check if already mapped
        existing_mapping = self.mapping_repo.read_by_bill_id(bill_id)
        if existing_mapping:
            logger.info(f"Bill {bill_id} is already mapped to QboBill {existing_mapping.qbo_bill_id}")
            return self.qbo_bill_repo.read_by_id(existing_mapping.qbo_bill_id)
        
        # Require bill_number — QBO DocNumber must be present; exclude_none=True would silently drop it
        if not bill.bill_number:
            raise ValueError(f"Bill {bill_id} has no bill_number. Set a bill number before syncing to QBO.")

        # Require bill_date — TxnDate must be present; without it QBO silently uses today's date
        if not bill.bill_date:
            raise ValueError(f"Bill {bill_id} has no bill_date. Set a bill date before syncing to QBO.")

        # Get QBO vendor reference
        qbo_vendor_ref = self._get_qbo_vendor_ref(bill.vendor_id)
        if not qbo_vendor_ref:
            raise ValueError(f"No QBO vendor mapping found for vendor_id: {bill.vendor_id}")
        
        # Get bill line items
        bill_line_items = self.bill_line_item_service.read_by_bill_id(bill_id=bill_id)
        
        # Build QBO line items — all line items must have valid mappings.
        # A partial sync is not allowed; if any line item cannot be mapped, the entire sync fails.
        qbo_lines = []
        line_num_to_line_item_id = {}

        if not bill_line_items:
            raise ValueError("Bill has no line items. QBO requires at least one line item.")

        for idx, line_item in enumerate(bill_line_items, start=1):
            qbo_line = self._build_qbo_line(line_item, idx)
            qbo_lines.append(qbo_line)
            line_num_to_line_item_id[idx] = line_item.id
        
        # Get AP Account reference
        ap_account_ref = self._get_ap_account_ref(realm_id)

        # Get SalesTerm reference from PaymentTerm mapping
        sales_term_ref = self._get_qbo_sales_term_ref(bill.payment_term_id, realm_id)

        # Build QBO Bill create payload
        qbo_bill_create = QboBillCreate(
            vendor_ref=qbo_vendor_ref,
            ap_account_ref=ap_account_ref,
            sales_term_ref=sales_term_ref,
            txn_date=bill.bill_date[:10] if bill.bill_date else None,  # YYYY-MM-DD
            due_date=bill.due_date[:10] if bill.due_date else None,
            doc_number=bill.bill_number,
            private_note=bill.memo,
            line=qbo_lines,
        )
        
        logger.info(f"Creating Bill in QBO for local Bill {bill_id}: doc_number={bill.bill_number}")

        # Log payload for debugging
        payload_dict = qbo_bill_create.model_dump(by_alias=True, exclude_none=True)
        logger.info(f"QBO Bill payload: {payload_dict}")

        from integrations.intuit.qbo.base.errors import QboDuplicateError

        # QboHttpClient (via QboBillClient) resolves and refreshes the access token
        # lazily, so no upfront auth call is needed here.
        with QboBillClient(realm_id=realm_id) as client:
            try:
                created_bill = client.create_bill(qbo_bill_create)
            except QboDuplicateError as e:
                # Bill already exists in QBO — record and fail loud. Adopt-style
                # recovery deliberately declined (see booked adopt unit in TODO.md).
                logger.warning(
                    f"Bill {bill_id} doc_number={bill.bill_number} already exists in QBO. "
                    f"Recording reconciliation issue and failing (no auto-adopt)."
                )
                self._recover_duplicate_qbo_bill(
                    bill=bill,
                    bill_id=bill_id,
                    realm_id=realm_id,
                    error=e,
                )

        logger.info(f"Created QBO Bill {created_bill.id} with SyncToken {created_bill.sync_token}")
        
        # Get vendor name for storage
        vendor = self.vendor_service.read_by_id(bill.vendor_id) if bill.vendor_id else None
        vendor_name = vendor.name if vendor else None
        
        # Store QboBill locally — reuse on retry if a prior attempt already persisted it
        existing_local_qbo_bill = self.qbo_bill_repo.read_by_qbo_id_and_realm_id(
            created_bill.id, realm_id
        )
        if existing_local_qbo_bill:
            conflicting_mapping = self.mapping_repo.read_by_qbo_bill_id(
                existing_local_qbo_bill.id
            )
            if conflicting_mapping:
                raise ValueError(
                    f"QboBill {existing_local_qbo_bill.id} (QboId={created_bill.id}) is already mapped to a "
                    f"different Bill {conflicting_mapping.bill_id}; cannot push Bill {bill_id} onto it. This "
                    f"indicates a race between this push retry and an independent pull, or a duplicate local "
                    f"Bill. Manual investigation required."
                )
            local_qbo_bill = existing_local_qbo_bill
            logger.info(
                f"QboBill already stored locally for QboId {created_bill.id} "
                f"(retry after prior partial success) — reusing local record {local_qbo_bill.id}"
            )
        else:
            local_qbo_bill = self.qbo_bill_repo.create(
                qbo_id=created_bill.id,
                sync_token=created_bill.sync_token,
                realm_id=realm_id,
                vendor_ref_value=qbo_vendor_ref.value,
                vendor_ref_name=vendor_name,
                txn_date=created_bill.txn_date,
                due_date=created_bill.due_date,
                doc_number=created_bill.doc_number,
                private_note=created_bill.private_note,
                total_amt=created_bill.total_amt,
                balance=created_bill.balance,
                ap_account_ref_value=created_bill.ap_account_ref.value if created_bill.ap_account_ref else None,
                ap_account_ref_name=created_bill.ap_account_ref.name if created_bill.ap_account_ref else None,
                sales_term_ref_value=created_bill.sales_term_ref.value if created_bill.sales_term_ref else None,
                sales_term_ref_name=created_bill.sales_term_ref.name if created_bill.sales_term_ref else None,
                currency_ref_value=created_bill.currency_ref.value if created_bill.currency_ref else None,
                currency_ref_name=created_bill.currency_ref.name if created_bill.currency_ref else None,
                exchange_rate=created_bill.exchange_rate,
                department_ref_value=created_bill.department_ref.value if created_bill.department_ref else None,
                department_ref_name=created_bill.department_ref.name if created_bill.department_ref else None,
                global_tax_calculation=created_bill.global_tax_calculation,
            )
            logger.info(f"Stored local QboBill {local_qbo_bill.id}")
        
        # Store QboBillLines locally and create line item mappings
        if created_bill.line:
            from integrations.intuit.qbo.bill.connector.bill_line_item.business.service import BillLineItemConnector
            line_connector = BillLineItemConnector()

            existing_lines_by_qbo_line_id = {
                line.qbo_line_id: line
                for line in self.qbo_bill_line_repo.read_by_qbo_bill_id(local_qbo_bill.id)
                if line.qbo_line_id
            }

            for qbo_line in created_bill.line:
                stored_line = (
                    existing_lines_by_qbo_line_id.get(qbo_line.id)
                    if qbo_line.id
                    else None
                )
                if not stored_line:
                    stored_line = self._store_qbo_bill_line(local_qbo_bill.id, qbo_line)

                # Create BillLineItem <-> QboBillLine mapping using line_num match
                if stored_line and qbo_line.line_num and qbo_line.line_num in line_num_to_line_item_id:
                    bill_line_item_id = line_num_to_line_item_id[qbo_line.line_num]
                    stored_line_id = coerce_id(stored_line.id)
                    try:
                        line_connector.create_mapping(
                            bill_line_item_id=bill_line_item_id,
                            qbo_bill_line_id=stored_line_id,
                        )
                        logger.info(f"Created line mapping: BillLineItem {bill_line_item_id} <-> QboBillLine {stored_line_id}")
                    except ValueError as e:
                        logger.warning(f"Could not create line mapping: {e}")
        
        # Create mapping
        qbo_bill_id = coerce_id(local_qbo_bill.id)
        try:
            mapping = self.create_mapping(
                bill_id=bill_id,
                qbo_bill_id=qbo_bill_id,
                qbo_id=local_qbo_bill.qbo_id,
                realm_id=local_qbo_bill.realm_id or realm_id,
                sync_token=getattr(local_qbo_bill, "sync_token", None),
            )
            logger.info(f"Created mapping: Bill {bill_id} <-> QboBill {qbo_bill_id}")
        except ValueError as e:
            logger.warning(f"Could not create mapping: {e}")
        
        return local_qbo_bill

    def _recover_duplicate_qbo_bill(
        self,
        bill: Bill,
        bill_id: int,
        realm_id: str,
        error: "QboDuplicateError",
    ) -> None:
        """
        Report-and-fail helper for duplicate DocNumber during push.

        Records a critical qbo.ReconciliationIssue and re-raises the typed
        QboDuplicateError — does NOT query QBO, adopt the existing bill, or
        create mappings. Adopt-style recovery is a separate reviewed unit
        (see TODO.md); a wrong adoption is permanent and invisible to daily reconcile.
        """
        from integrations.intuit.qbo.base.reconciliation_recorder import record_mapping_issue

        vendor = self.vendor_service.read_by_id(bill.vendor_id) if bill.vendor_id else None
        vendor_name = vendor.name if vendor else "unknown"

        details = (
            f"Duplicate QBO Bill DocNumber during push. Local Bill {bill_id} "
            f"(public_id={bill.public_id}, bill_number={bill.bill_number}) for vendor "
            f"'{vendor_name}' collides with an existing QBO Bill. Adopt-style recovery "
            f"deliberately declined — see booked adopt unit (TODO.md). Manual link required."
        )
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="duplicate_qbo_bill_docnumber",
            entity_type="Bill",
            entity_public_id=str(bill.public_id) if bill.public_id else None,
            qbo_id=None,
            realm_id=realm_id,
            details=details,
            severity="critical",
        )
        raise error

    def update_has_been_billed_in_qbo(self, bill_id: int, realm_id: str) -> None:
        """
        Re-push a QBO Bill with updated BillableStatus = HasBeenBilled on billed line items.
        Called after invoice completion to reflect the billed state in QBO.
        """
        from integrations.intuit.qbo.bill.external.schemas import QboBillUpdate

        mapping = self.mapping_repo.read_by_bill_id(bill_id)
        if not mapping:
            logger.debug(f"No QBO mapping for bill_id={bill_id}, skipping HasBeenBilled update")
            return

        local_qbo_bill = self.qbo_bill_repo.read_by_id(mapping.qbo_bill_id)
        if not local_qbo_bill or not local_qbo_bill.qbo_id:
            return

        # Auth is resolved lazily inside QboHttpClient when the bill client makes a request.
        # Rebuild all bill lines — _build_qbo_line reads is_billed from local DB,
        # so billed items will now get BillableStatus = "HasBeenBilled".
        # Use sequential line_nums with no gaps to match QBO's numbering.
        bill_line_items = self.bill_line_item_service.read_by_bill_id(bill_id=bill_id)
        qbo_lines = []
        seq = 0
        for line_item in bill_line_items:
            qbo_line = self._build_qbo_line(line_item, seq + 1)
            if qbo_line:
                seq += 1
                qbo_lines.append(qbo_line)

        if not qbo_lines:
            logger.warning(f"No QBO lines could be built for bill_id={bill_id}, skipping update")
            return

        vendor_ref = QboReferenceType(
            value=local_qbo_bill.vendor_ref_value,
            name=local_qbo_bill.vendor_ref_name,
        )

        # QBO Bill updates are full-replace — any field not included is cleared.
        # Re-send all header fields from the locally stored QboBill to preserve them.
        ap_account_ref = (
            QboReferenceType(value=local_qbo_bill.ap_account_ref_value, name=local_qbo_bill.ap_account_ref_name)
            if local_qbo_bill.ap_account_ref_value else None
        )
        sales_term_ref = (
            QboReferenceType(value=local_qbo_bill.sales_term_ref_value, name=local_qbo_bill.sales_term_ref_name)
            if local_qbo_bill.sales_term_ref_value else None
        )
        currency_ref = (
            QboReferenceType(value=local_qbo_bill.currency_ref_value, name=local_qbo_bill.currency_ref_name)
            if local_qbo_bill.currency_ref_value else None
        )
        department_ref = (
            QboReferenceType(value=local_qbo_bill.department_ref_value, name=local_qbo_bill.department_ref_name)
            if local_qbo_bill.department_ref_value else None
        )

        with QboBillClient(realm_id=realm_id) as client:
            fresh = client.get_bill(local_qbo_bill.qbo_id)
            qbo_bill_update = QboBillUpdate(
                id=local_qbo_bill.qbo_id,
                sync_token=fresh.sync_token,
                vendor_ref=vendor_ref,
                ap_account_ref=ap_account_ref,
                sales_term_ref=sales_term_ref,
                currency_ref=currency_ref,
                department_ref=department_ref,
                txn_date=local_qbo_bill.txn_date,
                due_date=local_qbo_bill.due_date,
                doc_number=local_qbo_bill.doc_number,
                private_note=local_qbo_bill.private_note,
                exchange_rate=local_qbo_bill.exchange_rate,
                global_tax_calculation=local_qbo_bill.global_tax_calculation,
                line=qbo_lines,
            )
            updated = client.update_bill(qbo_bill_update)

        logger.info(f"Updated QBO Bill {local_qbo_bill.qbo_id} — billed line items now HasBeenBilled")

        # Persist the new SyncToken locally
        self.qbo_bill_repo.update_by_qbo_id(
            qbo_id=local_qbo_bill.qbo_id,
            row_version=local_qbo_bill.row_version_bytes,
            sync_token=updated.sync_token,
            realm_id=realm_id,
            vendor_ref_value=local_qbo_bill.vendor_ref_value,
            vendor_ref_name=local_qbo_bill.vendor_ref_name,
            txn_date=local_qbo_bill.txn_date,
            due_date=local_qbo_bill.due_date,
            doc_number=local_qbo_bill.doc_number,
            private_note=local_qbo_bill.private_note,
            total_amt=updated.total_amt,
            balance=updated.balance,
            ap_account_ref_value=local_qbo_bill.ap_account_ref_value,
            ap_account_ref_name=local_qbo_bill.ap_account_ref_name,
            sales_term_ref_value=local_qbo_bill.sales_term_ref_value,
            sales_term_ref_name=local_qbo_bill.sales_term_ref_name,
            currency_ref_value=local_qbo_bill.currency_ref_value,
            currency_ref_name=local_qbo_bill.currency_ref_name,
            exchange_rate=local_qbo_bill.exchange_rate,
            department_ref_value=local_qbo_bill.department_ref_value,
            department_ref_name=local_qbo_bill.department_ref_name,
            global_tax_calculation=local_qbo_bill.global_tax_calculation,
        )

    # One of FIVE near-identical dbo-first/legacy-fallback vendor-ref resolvers
    # (U-284v): this one (the only push-side one), this file's own
    # _get_vendor_public_id (pull), PurchaseExpenseConnector._get_vendor_public_id,
    # VendorCreditBillCreditConnector._get_vendor_public_id,
    # ExpenseCodingItemService._resolve_vendor_id. Hand-copied deliberately,
    # mirroring _get_project_public_id's own precedent — see TODO.md's
    # U-005[reuse] entry before adding a 6th copy or consolidating.
    def _get_qbo_vendor_ref(self, vendor_id: int) -> Optional[QboReferenceType]:
        """
        Get QBO VendorRef from local vendor_id.
        
        Args:
            vendor_id: Local vendor database ID
            
        Returns:
            QboReferenceType with QBO vendor value and name, or None
        """
        if not vendor_id:
            return None

        # U-284v: fetched once and reused by both branches below (the direct
        # attempt needs it for verification + name; the legacy hop's own
        # name lookup used to fetch it a second time, only after both of its
        # own lookups succeeded — sharing this one fetch instead means a
        # vendor with no VendorVendor mapping yet now costs one extra Vendor
        # read on the legacy hop's two failure branches, which previously
        # made zero calls to it. Deliberate tradeoff: the direct-hit case
        # this repoint exists to speed up is the common one going forward.
        vendor = self.vendor_service.read_by_id(vendor_id)

        # Try dbo.Vendor's native QboId/Name directly first, verified against
        # the qbo.VendorVendor mapping before trusting it for an outbound
        # push — a stale/"stolen" dbo QboId must never misroute a live Bill
        # to the wrong QBO vendor (mirrors U-276 round-4's push-side finding
        # for Project; see identity_consistency.py).
        if vendor:
            verified_qbo_id = verify_vendor_qbo_identity(
                vendor,
                vendor_vendor_repo=self.vendor_vendor_repo,
                qbo_vendor_repo=self.qbo_vendor_repo,
            )
            if verified_qbo_id:
                return QboReferenceType(value=verified_qbo_id, name=vendor.name)

        # Legacy mapping-table hop (miss or unverified dbo identity) — the
        # hop's own two lookups are unchanged; only the preceding vendor
        # fetch above is new (see comment there).
        vendor_mapping = self.vendor_vendor_repo.read_by_vendor_id(vendor_id)
        if not vendor_mapping:
            logger.warning(f"VendorVendor mapping not found for vendor_id: {vendor_id}")
            return None

        # Get QboVendor
        qbo_vendor = self.qbo_vendor_repo.read_by_id(vendor_mapping.qbo_vendor_id)
        if not qbo_vendor or not qbo_vendor.qbo_id:
            logger.warning(f"QboVendor not found for qbo_vendor_id: {vendor_mapping.qbo_vendor_id}")
            return None

        vendor_name = vendor.name if vendor else None

        return QboReferenceType(value=qbo_vendor.qbo_id, name=vendor_name)

    def _get_qbo_item_ref(self, sub_cost_code_id: int) -> Optional[QboReferenceType]:
        """
        Get QBO ItemRef from local sub_cost_code_id.
        
        Args:
            sub_cost_code_id: Local SubCostCode database ID
            
        Returns:
            QboReferenceType with QBO item value and name, or None
        """
        if not sub_cost_code_id:
            logger.debug("_get_qbo_item_ref called with None sub_cost_code_id")
            return None
        
        # Find ItemSubCostCode mapping
        logger.debug(f"Looking up ItemSubCostCode mapping for sub_cost_code_id: {sub_cost_code_id}")
        item_mapping = self.item_sub_cost_code_repo.read_by_sub_cost_code_id(sub_cost_code_id)
        if not item_mapping:
            logger.warning(f"ItemSubCostCode mapping not found for sub_cost_code_id: {sub_cost_code_id}")
            return None
        
        # Get QboItem
        qbo_item = self.qbo_item_repo.read_by_id(item_mapping.qbo_item_id)
        if not qbo_item or not qbo_item.qbo_id:
            logger.debug(f"QboItem not found for qbo_item_id: {item_mapping.qbo_item_id}")
            return None
        
        return QboReferenceType(value=qbo_item.qbo_id, name=qbo_item.name)

    def _get_qbo_customer_ref(self, project_id: int) -> Optional[QboReferenceType]:
        """
        Get QBO CustomerRef from local project_id.

        U-276 (Phase-4 pilot): reads dbo.Project.Name/.QboId directly (native
        since U-238a) instead of hopping qbo.CustomerProject -> qbo.Customer
        for DisplayName. Returns None if the Project has never been QBO-synced
        (no QboId stamped) — same "not mapped, don't push" contract as before.
        The dbo identity is verified against the mapping table before being
        trusted (round-4 review) — dbo-internal uniqueness alone doesn't
        guarantee the mapping table has caught up to the latest holder.

        Args:
            project_id: Local Project database ID

        Returns:
            QboReferenceType with QBO customer value and name, or None
        """
        if not project_id:
            return None

        project = self.project_service.read_by_id(project_id)
        if not project or not project.qbo_id:
            logger.debug(f"Project {project_id} has no QBO identity (QboId) stamped")
            return None

        verified_qbo_id = verify_project_qbo_identity(
            project,
            customer_project_repo=self.customer_project_repo,
            qbo_customer_repo=self.qbo_customer_repo,
        )
        if not verified_qbo_id:
            return None

        return QboReferenceType(value=verified_qbo_id, name=project.name)

    def _get_ap_account_ref(self, realm_id: str) -> Optional[QboReferenceType]:
        """
        Get the Accounts Payable account reference for a realm.

        Reads dbo.Company's cached AP-account fields FIRST (U-281) —
        populated by QboAccountService's scheduled qbo.Account pull, which
        re-derives "the Accounts-Payable-type account for this realm" after
        every batch, so this no longer scans qbo.Account on every live Bill
        push. Falls back to that same live scan when the Company row hasn't
        been backfilled/pulled yet for this realm (old-container-safe during
        the rollout window, same shape U-275 used for the QboActive mirror)
        — a live Bill push must not break because the cache is merely empty,
        OR because the cache read itself failed (a transient dbo.Company
        error is unrelated to qbo.Account and must not newly break a push
        that never touched dbo.Company before this repoint). **U-296**:
        proved this fallback is still real defense (not migration
        scaffolding) — it's what keeps a live push from hard-failing for any
        realm that hasn't had an Account pull run yet — so it stays, now
        reading the same server-side-filtered query the cache derivation
        uses (`read_by_realm_id_and_account_type`) instead of a full-mirror
        Python scan.

        Args:
            realm_id: QBO realm ID

        Returns:
            QboReferenceType with AP account value and name, or None
        """
        try:
            company = self.company_service.read_by_realm_id(realm_id)
        except Exception as e:
            logger.warning(f"Failed to read cached AP account for realm_id {realm_id}: {e}")
            company = None

        if company and company.ap_account_qbo_id:
            return QboReferenceType(value=company.ap_account_qbo_id, name=company.ap_account_name)

        # Fallback: cache not yet populated for this realm — scan qbo.Account
        # directly (server-side filtered to AccountType, U-296), same
        # select_ap_account() this method used before the U-281 repoint.
        accounts = self.qbo_account_repo.read_by_realm_id_and_account_type(realm_id, AP_ACCOUNT_TYPE)
        ap_account = select_ap_account(accounts)
        if ap_account:
            return QboReferenceType(value=ap_account.qbo_id, name=ap_account.name)

        logger.warning(f"No Accounts Payable account found for realm_id: {realm_id}")
        return None

    def _get_qbo_sales_term_ref(self, payment_term_id: int, realm_id: str) -> Optional[QboReferenceType]:
        """
        Get QBO SalesTermRef from local payment_term_id.

        Reads dbo.PaymentTerm's native QboId/Name (U-282) FIRST — the
        Phase-6 readiness audit's highest live-traffic single-family gap,
        firing on every completed Bill's QBO push. Realm-verified before
        being trusted (multi-realm safety, matching this file's other
        dbo-first resolvers), then falls back to the legacy
        qbo.TermPaymentTerm -> qbo.Term two-hop on a miss, realm mismatch,
        or a transient dbo.PaymentTerm read failure (same "must not newly
        break a push that never touched this table before" rationale as
        _get_ap_account_ref's cache read above — the legacy hop is still a
        real, working fallback and a dbo-side error is unrelated to it).

        `name` is sent to QBO as display-only text on SalesTermRef (QBO
        resolves the actual term link purely off `value`, the QboId) —
        the dbo-first path returns dbo.PaymentTerm.Name, which a human can
        rename locally (accepted residual, same "never clobber curation"
        policy as preserve_human_edited_ref elsewhere in this package) and
        which can therefore differ from qbo.Term's own mirrored name the
        legacy hop would have sent. Deliberate, not a defect.

        Args:
            payment_term_id: Local PaymentTerm database ID
            realm_id: QBO realm ID the outbound push targets

        Returns:
            QboReferenceType with QBO term value and name, or None
        """
        if not payment_term_id:
            return None

        try:
            payment_term = self.payment_term_service.read_by_id(payment_term_id)
        except Exception as e:
            logger.warning(f"Failed to read dbo.PaymentTerm for payment_term_id {payment_term_id}: {e}")
            payment_term = None

        if payment_term and payment_term.qbo_id and payment_term.realm_id == realm_id:
            return QboReferenceType(value=payment_term.qbo_id, name=payment_term.name)

        # Legacy mapping-table hop — dbo not yet stamped for this term, or
        # its RealmId doesn't match this push's realm.
        term_mapping = self.term_payment_term_repo.read_by_payment_term_id(payment_term_id)
        if not term_mapping:
            logger.debug(f"TermPaymentTerm mapping not found for payment_term_id: {payment_term_id}")
            return None

        # Get QboTerm
        qbo_term = self.qbo_term_repo.read_by_id(term_mapping.qbo_term_id)
        if not qbo_term or not qbo_term.qbo_id:
            logger.debug(f"QboTerm not found for qbo_term_id: {term_mapping.qbo_term_id}")
            return None

        return QboReferenceType(value=qbo_term.qbo_id, name=qbo_term.name)

    def _build_qbo_line(self, line_item, line_num: int) -> Optional[QboBillLineSchema]:
        """
        Build a QBO Bill line from a local BillLineItem.
        
        Args:
            line_item: BillLineItem record
            line_num: Line number
            
        Returns:
            QboBillLineSchema or None
        """
        logger.debug(f"Building QBO line for BillLineItem {line_item.id}: sub_cost_code_id={line_item.sub_cost_code_id}, project_id={line_item.project_id}")
        
        # Get QBO references — all line items must have valid mappings
        item_ref = None
        if line_item.sub_cost_code_id:
            item_ref = self._get_qbo_item_ref(line_item.sub_cost_code_id)
            if not item_ref:
                raise ValueError(
                    f"BillLineItem {line_item.id}: no QBO Item mapping for sub_cost_code_id={line_item.sub_cost_code_id}. "
                    f"Map the SubCostCode to a QBO Item before syncing."
                )
        else:
            raise ValueError(f"BillLineItem {line_item.id} has no sub_cost_code_id. All line items require a SubCostCode for QBO sync.")
        
        customer_ref = self._get_qbo_customer_ref(line_item.project_id) if line_item.project_id else None
        
        # Determine billable status.
        # is_billable=None means default billable (treat same as True).
        # is_billable=False means explicitly not billable.
        # Note: If BillableStatus is "Billable", CustomerRef is REQUIRED by QBO.
        if line_item.is_billable is not False:
            if customer_ref:
                billable_status = "HasBeenBilled" if line_item.is_billed is True else "Billable"
            elif line_item.project_id:
                # Fail loud: a billable line WITH a project but no QBO CustomerRef mapping must not
                # be silently downgraded to NotBillable (that silent downgrade let 14 OVH bills push
                # in the wrong state unnoticed, 2026-07). Raise like the missing-Item-mapping path
                # above so the push dead-letters via the outbox instead of shipping a not-billable bill.
                raise ValueError(
                    f"BillLineItem {line_item.id}: billable line on project_id={line_item.project_id} "
                    f"has no QBO CustomerRef mapping. Map the Project to a qbo.CustomerProject row before syncing."
                )
            else:
                # Billable but no project to bill against — nothing to map; keep NotBillable.
                logger.warning(
                    f"Line item {line_item.id} is billable but has no project_id; setting to NotBillable."
                )
                billable_status = "NotBillable"
        else:
            billable_status = "NotBillable"
        
        # Calculate markup percent (convert from decimal like 0.10 to percentage like 10)
        markup_percent = None
        if line_item.markup is not None:
            markup_percent = line_item.markup * Decimal('100')
        
        # Item-based expense line
        # Ensure we have an amount - QBO requires either Amount or (Qty + UnitPrice)
        line_amount = line_item.amount
        qty = Decimal(str(line_item.quantity)) if line_item.quantity else None
        unit_price = line_item.rate

        # If no amount, try to calculate from qty * rate
        if line_amount is None and qty is not None and unit_price is not None:
            line_amount = qty * unit_price

        # If still no amount, use 0 as fallback
        if line_amount is None:
            logger.warning(f"Line item {line_item.id} has no amount, qty, or rate. Using 0.")
            line_amount = Decimal('0')

        detail = QboItemBasedExpenseLineDetail(
            item_ref=item_ref,
            customer_ref=customer_ref,
            billable_status=billable_status,
            qty=qty,
            unit_price=unit_price,
            # float() is acceptable here: percentage value inside Dict[str, Any] needs
            # JSON-serializable numeric type; Pydantic won't auto-convert Decimal in dicts.
            markup_info={"Percent": float(markup_percent)} if markup_percent is not None else None,
        )
        return QboBillLineSchema(
            line_num=line_num,
            description=line_item.description,
            amount=line_amount,
            detail_type="ItemBasedExpenseLineDetail",
            item_based_expense_line_detail=detail,
        )

    def _store_qbo_bill_line(self, qbo_bill_id: int, qbo_line: QboBillLineSchema):
        """
        Store a QBO Bill line locally.

        Args:
            qbo_bill_id: Local QboBill database ID
            qbo_line: QBO Bill line from API response

        Returns:
            QboBillLine: The created local record, or None on failure
        """
        try:
            # Extract references from line detail
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
            
            if qbo_line.item_based_expense_line_detail:
                detail = qbo_line.item_based_expense_line_detail
                if detail.item_ref:
                    item_ref_value = detail.item_ref.value
                    item_ref_name = detail.item_ref.name
                if detail.customer_ref:
                    customer_ref_value = detail.customer_ref.value
                    customer_ref_name = detail.customer_ref.name
                if detail.class_ref:
                    class_ref_value = detail.class_ref.value
                    class_ref_name = detail.class_ref.name
                billable_status = detail.billable_status
                qty = detail.qty
                unit_price = detail.unit_price
                if detail.markup_info and isinstance(detail.markup_info, dict):
                    raw_pct = detail.markup_info.get("Percent") or detail.markup_info.get("percent")
                    if raw_pct is not None:
                        markup_percent = Decimal(str(raw_pct))
            elif qbo_line.account_based_expense_line_detail:
                detail = qbo_line.account_based_expense_line_detail
                if detail.account_ref:
                    account_ref_value = detail.account_ref.value
                    account_ref_name = detail.account_ref.name
                if detail.customer_ref:
                    customer_ref_value = detail.customer_ref.value
                    customer_ref_name = detail.customer_ref.name
                if detail.class_ref:
                    class_ref_value = detail.class_ref.value
                    class_ref_name = detail.class_ref.name
                billable_status = detail.billable_status
            
            return self.qbo_bill_line_repo.create(
                qbo_bill_id=qbo_bill_id,
                qbo_line_id=qbo_line.id,
                line_num=qbo_line.line_num,
                description=qbo_line.description,
                amount=qbo_line.amount,
                detail_type=qbo_line.detail_type,
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
        except Exception as e:
            logger.error(f"Failed to store QboBillLine: {e}")
            return None
