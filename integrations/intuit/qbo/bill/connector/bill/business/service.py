# Python Standard Library Imports
import logging
from typing import List, Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
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
from integrations.intuit.qbo.vendor.persistence.repo import QboVendorRepository
from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository
from integrations.intuit.qbo.account.persistence.repo import QboAccountRepository
from integrations.intuit.qbo.account.business.service import AP_ACCOUNT_TYPE, select_ap_account
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
from integrations.intuit.qbo.base.identity_consistency import verify_identity_dbo_only
from integrations.intuit.qbo.base.identity_fastpath import run_identity_fastpath_dbo_only
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.reconciliation_recorder import record_mapping_issue
from integrations.intuit.qbo.base.cost_code_resolver import resolve_qbo_item_ref
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from entities.sub_cost_code.business.service import SubCostCodeService

logger = logging.getLogger(__name__)


class BillBillConnector:
    """
    Connector service for synchronization between QboBill and Bill modules.

    U-355: dbo-only identity resolution via `run_identity_fastpath_dbo_only` --
    no `qbo.BillBill` mapping-table read/write of any kind (U-349 program family
    6/11, the heaviest header connector -- mirrors U-350's `CompanyInfoCompanyConnector`
    / U-353's `VendorCreditBillCreditConnector` / U-354's `PurchaseExpenseConnector`,
    per Wave 5's "trust dbo alone" plan,
    `docs/design/u349-qbo-mapping-table-retirement.md`). `dbo.Bill.QboId`/`RealmId`
    (U-238a) is the sole identity store; dbo.Bill's own filtered unique index
    (`UQ_Bill_QboId_RealmId`) + `SetBillQboIdentity`'s theft-clear UPDATE guarantee
    at most one row holds a given identity at any instant, so a direct hit needs no
    cross-check and the old heal/adopt-by-fingerprint branch structure (driven by a
    second, independently-writable mapping table) no longer has anything to drift
    from. Like VendorCredit, Bill has its own natural CREATE-dedup
    (`UQ_Bill_VendorId_BillNumber_BillDate`), so a genuine miss is a plain create --
    the DB constraint, not a mapping-table check, is what protects against a
    concurrent duplicate.

    Unlike Expense/VendorCredit/PhysicalAddress/Term/CompanyInfo, Bill's PUSH path
    (`sync_to_qbo_bill`, dispatched by the outbox worker's live `sync_bill_to_qbo`
    Kind) is real, high-volume traffic (918 live rows as of this unit) -- it is
    repointed onto dbo-native identity below (see `sync_to_qbo_bill` and
    `update_has_been_billed_in_qbo`), not deleted the way Expense's dead
    `sync_expense_to_qbo` push was in U-354.
    """

    def __init__(
        self,
        bill_service: Optional[BillService] = None,
        vendor_service: Optional[VendorService] = None,
        vendor_vendor_repo=None,
        qbo_vendor_repo: Optional[QboVendorRepository] = None,
        qbo_bill_repo: Optional[QboBillRepository] = None,
        qbo_bill_line_repo: Optional[QboBillLineRepository] = None,
        bill_line_item_service: Optional[BillLineItemService] = None,
        customer_project_repo=None,
        qbo_customer_repo: Optional[QboCustomerRepository] = None,
        project_service: Optional[ProjectService] = None,
        qbo_account_repo: Optional[QboAccountRepository] = None,
        term_payment_term_repo=None,
        qbo_term_repo=None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
        company_service: Optional[CompanyService] = None,
        payment_term_service: Optional[PaymentTermService] = None,
        sub_cost_code_service: Optional[SubCostCodeService] = None,
    ):
        """Initialize the BillBillConnector."""
        self.bill_service = bill_service or BillService()
        self.vendor_service = vendor_service or VendorService()
        # U-313: no longer read anywhere in this file (_get_vendor_public_id/
        # _get_qbo_vendor_ref both moved fully dbo-only, no qbo.VendorVendor
        # hop left). U-314 dropped qbo.VendorVendor entirely, so this can no
        # longer default-construct its old repo class — kept as an untyped,
        # unconstructed constructor param so the ~10 existing test call sites
        # across other units that still pass vendor_vendor_repo=/
        # qbo_vendor_repo= don't need to change for a unit whose real scope
        # is the Vendor mapping table, not this connector's constructor —
        # see TODO.md's U-313 follow-ups.
        self.vendor_vendor_repo = vendor_vendor_repo
        self.qbo_vendor_repo = qbo_vendor_repo or QboVendorRepository()
        self.qbo_bill_repo = qbo_bill_repo or QboBillRepository()
        self.qbo_bill_line_repo = qbo_bill_line_repo or QboBillLineRepository()
        self.bill_line_item_service = bill_line_item_service or BillLineItemService()
        # U-311: no longer read anywhere in this file (the legacy qbo.Customer
        # -> qbo.CustomerProject hop was deleted from this connector's own
        # project-ref resolution). U-314 dropped qbo.CustomerProject entirely,
        # so this can no longer default-construct its old repo class — kept
        # as an untyped, unconstructed constructor param so the ~15 existing
        # test call sites across other units that still pass
        # customer_project_repo=/qbo_customer_repo= don't need to change for
        # a unit whose real scope is the Project mapping table, not this
        # connector's constructor.
        self.customer_project_repo = customer_project_repo
        self.qbo_customer_repo = qbo_customer_repo or QboCustomerRepository()
        self.project_service = project_service or ProjectService()
        self.qbo_account_repo = qbo_account_repo or QboAccountRepository()
        # U-352: neither attribute is read anywhere in this file anymore
        # (_get_qbo_sales_term_ref's qbo.TermPaymentTerm two-hop fallback was
        # removed — a dbo-native miss now returns None instead of falling back to a
        # second store). `term_payment_term_repo`'s old repo class
        # (TermPaymentTermRepository) was retired outright, so it can no longer be
        # default-constructed; `qbo_term_repo`'s class (QboTermRepository) still
        # exists but nothing here reads it either, so building one on every
        # instantiation would be pure waste. Both kept as untyped, unconstructed
        # constructor params so the existing test call sites across other units
        # that still pass term_payment_term_repo=/qbo_term_repo= don't need to
        # change for a unit whose real scope is the PaymentTerm mapping table, not
        # this connector's constructor (mirrors U-313's vendor_vendor_repo / U-311's
        # customer_project_repo precedent above).
        self.term_payment_term_repo = term_payment_term_repo
        self.qbo_term_repo = qbo_term_repo
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()
        self.company_service = company_service or CompanyService()
        self.payment_term_service = payment_term_service or PaymentTermService()
        # U-307b: only ever passed to cost_code_resolver.resolve_qbo_item_ref, never
        # used directly here. Kept as an injectable constructor param (not defaulted
        # inline) so tests can inject a fake exactly as they did before this repoint.
        self.sub_cost_code_service = sub_cost_code_service

    def sync_from_qbo_bill(self, qbo_bill: QboBill, qbo_bill_lines: List[QboBillLine]) -> Bill:
        """
        Sync a QBO Bill to the Bill module, via the dbo-only identity fast path
        (U-355).

        This method:
        1. Resolves the vendor mapping to get a Vendor public_id (dbo-native)
        2. Direct dbo.Bill.QboId/RealmId hit -> update in place
        3. Genuine miss -> create a new Bill, stamp identity, sync lines
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

        def _apply_bill_fields(direct: Bill) -> Optional[Bill]:
            """
            `apply_fields` for the dbo-only fast path's HIT branch (U-355): write the
            QBO-derived fields onto an existing dbo-identity-matched Bill, persist,
            and sync its line items. Covers both a plain direct hit and a
            race-resolved hit (run_identity_fastpath_dbo_only calls this same
            callback for both).

            U-027 (rule of three): never clobber a human-corrected bill_number on
            re-pull. Preserve the stored value unless it is empty/null or the
            QBO-<id> placeholder (which still upgrades to a real doc_number). See
            base.field_ownership.

            Bill carries SyncToken as part of its identity (like Expense/Purchase) --
            this re-stamp is NOT skipped on a plain HIT: it refreshes SyncToken on
            every pull, matching the pre-U-355 behavior exactly.

            Returns None on a ROWVERSION-race/concurrent-delete `update_by_public_id`
            miss (U-291) -- `run_identity_fastpath_dbo_only`'s own `_apply()` raises
            `raise_concurrent_write_race` unconditionally whenever `apply_fields`
            returns None, so this method staying silent on a miss (and skipping line
            sync) is what keeps that single raise as the ONE place the guarantee lives.
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
                return None
            bill_id = coerce_id(updated.id)
            self.bill_service.repo.set_qbo_identity(
                id=bill_id,
                qbo_id=qbo_bill.qbo_id,
                realm_id=qbo_bill.realm_id,
                sync_token=getattr(qbo_bill, "sync_token", None),
            )
            self._sync_line_items(bill_id, qbo_bill_lines, qbo_bill.realm_id)
            return updated

        outcome = run_identity_fastpath_dbo_only(
            qbo_id=qbo_bill.qbo_id,
            realm_id=qbo_bill.realm_id,
            entity_label="Bill",
            external_label="QboBill",
            lock_resource_label="Bill",
            read_direct_by_qbo_identity=self.bill_service.read_by_qbo_identity,
            apply_fields=_apply_bill_fields,
            resolve_candidate=lambda: self._create_bill(
                qbo_bill=qbo_bill,
                vendor_public_id=vendor_public_id,
                bill_number=bill_number,
                bill_date=bill_date,
                due_date=due_date,
                memo=memo,
                total_amount=total_amount,
            ),
            stamp_identity=lambda candidate: self._stamp_bill_identity(
                candidate, qbo_bill=qbo_bill, qbo_bill_lines=qbo_bill_lines,
            ),
        )
        if outcome.entity is None:
            # No longer race-reachable in practice (see run_identity_fastpath_
            # dbo_only's Raises docstring) — kept as a backstop for a directly
            # invoked falsy qbo_bill.qbo_id, mirroring every sibling connector's
            # identical guard (U-350/U-353/U-354).
            raise RuntimeError(
                f"Failed to resolve Bill for QboBill {qbo_bill.id} "
                f"(qbo_id={qbo_bill.qbo_id}) via the dbo-only identity fast path"
            )
        return outcome.entity

    def _create_bill(
        self,
        *,
        qbo_bill: QboBill,
        vendor_public_id: str,
        bill_number: str,
        bill_date: str,
        due_date: str,
        memo,
        total_amount,
    ) -> Optional[Bill]:
        """
        `resolve_candidate` for the dbo-only fast path's MISS branch (U-355):
        called only under `run_identity_fastpath_dbo_only`'s create lock, once a
        genuine miss is confirmed (no dbo.Bill currently holds this identity,
        including the re-read under lock). Mirrors the pre-U-355 legacy CREATE
        step exactly; `UQ_Bill_VendorId_BillNumber_BillDate` (not a mapping-table
        check) is what protects against a concurrent duplicate.
        """
        logger.info(f"Creating new Bill from QboBill {qbo_bill.id}: bill_number={bill_number}")
        return self.bill_service.create(
            vendor_public_id=vendor_public_id,
            bill_date=bill_date,
            due_date=due_date,
            bill_number=bill_number,
            total_amount=total_amount,
            memo=memo,
            is_draft=False,
            # QBO-origin bills have no local PDF; the universal attachment rule
            # does not apply. Line items are created by _stamp_bill_identity's
            # own _sync_line_items call below, not by create()'s
            # placeholder-attachment path.
            require_attachment=False,
        )

    def _stamp_bill_identity(
        self,
        candidate: Optional[Bill],
        *,
        qbo_bill: QboBill,
        qbo_bill_lines: List[QboBillLine],
    ) -> Optional[Bill]:
        """
        `stamp_identity` for the dbo-only fast path's MISS branch (U-355): stamp
        dbo-native identity onto the just-created Bill, then sync its line items.
        `candidate` is a fresh, uniquely-ours row from `_create_bill` (not a
        side-channel-key match shared with any other incoming QBO record), so —
        unlike Company's by-name candidate — there is no concurrent-different-
        qbo_id race to guard with extra locking; `run_identity_fastpath_dbo_only`'s
        own create lock already serializes two syncs of the SAME QboBill against
        each other.

        On a permanent failure in EITHER the identity stamp or the line sync,
        best-effort deletes the just-created header via `rollback_orphan_header`
        so a bad create never strands a header-only zombie -- the identity-stamp
        rollback race fix (U-354 pattern): before this fix, a transient
        `set_qbo_identity` failure during CREATE could mint an unstamped orphan
        Bill that `read_direct_by_qbo_identity` can never find again (it carries
        no QboId), and the next pull tick would mint a genuine duplicate. Both
        steps share ONE try/except (not two) so a `set_qbo_identity` failure gets
        the exact same cleanup as a line-sync failure.

        Re-reads and returns the row after stamping (mirrors
        `PurchaseExpenseConnector._stamp_expense_identity`, U-354):
        `set_qbo_identity` is a void DB write that never mutates `candidate` in
        memory, so returning `candidate` as-is would hand the caller a Bill whose
        `qbo_id`/`realm_id` still read as their pre-stamp `None` even though the
        DB row is correctly stamped.
        """
        if candidate is None:
            return None

        bill_id = coerce_id(candidate.id)
        try:
            self.bill_service.repo.set_qbo_identity(
                id=bill_id,
                qbo_id=qbo_bill.qbo_id,
                realm_id=qbo_bill.realm_id,
                sync_token=getattr(qbo_bill, "sync_token", None),
            )
            self._sync_line_items(bill_id, qbo_bill_lines, qbo_bill.realm_id)
        except Exception:
            rollback_orphan_header(
                delete_header=lambda: self.bill_service.delete_by_public_id(candidate.public_id),
                delete_mapping=lambda: None,
                entity_label='Bill', entity_id=bill_id,
                on_header_delete_failed=lambda exc: self._record_orphan_header_issue(
                    bill=candidate, qbo_bill=qbo_bill, exc=exc
                ),
            )
            raise

        return self.bill_service.read_by_id(bill_id)

    def _record_orphan_header_issue(
        self,
        *,
        bill: Bill,
        qbo_bill: QboBill,
        exc: Exception,
    ) -> None:
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="orphan_bill_header",
            entity_type="Bill",
            entity_public_id=str(bill.public_id) if bill.public_id else None,
            qbo_id=str(qbo_bill.qbo_id) if qbo_bill.qbo_id else None,
            realm_id=qbo_bill.realm_id or "",
            details=(
                f"Compensating rollback failed to delete orphan Bill {bill.id} "
                f"({bill.public_id}): {exc}. Header blocks re-pull until manually resolved."
            ),
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

        # U-313: dbo.Vendor's native QboId/RealmId is now the SOLE identity
        # store for Vendor (Wave 5 "trust dbo alone" — qbo.VendorVendor no
        # longer has a writer, see docs/design/wave5.md). A miss here means
        # no Vendor was ever synced under this identity — there is no legacy
        # hop left to fall back to (removed; it had no data source left
        # either, per the same reasoning).
        direct_vendor = self.vendor_service.read_by_qbo_identity(qbo_vendor_ref_value, realm_id)
        if direct_vendor:
            verified_qbo_id = verify_identity_dbo_only(
                direct_vendor,
                read_direct_by_qbo_identity=self.vendor_service.read_by_qbo_identity,
            )
            if verified_qbo_id:
                return direct_vendor.public_id

        return None

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

    def sync_to_qbo_bill(self, bill: Bill, realm_id: str) -> QboBill:
        """
        Sync a local Bill to QuickBooks Online, via the dbo-only identity fast
        path (U-355). Live push traffic (the outbox worker's `sync_bill_to_qbo`
        Kind, 918 rows as of this unit) -- repointed, not retired.

        dbo.Bill.QboId/RealmId is the sole "already pushed" signal now; there is
        no qbo.BillBill mapping row to check. A truthy `bill.qbo_id` is
        re-verified via `verify_identity_dbo_only` before being trusted for the
        qbo.Bill staging-cache lookup below (mirrors `outbox/business/worker.py
        ::_refresh_bill`'s own repoint) -- a stale/reassigned identity must never
        resolve to the WRONG cached QboBill.

        This method:
        1. Checks whether the Bill is already pushed (dbo-native identity, verified)
        2. Looks up vendor mapping to get QBO vendor reference
        3. Builds QBO Bill payload with line items
        4. Creates Bill in QBO via API
        5. Stores QboBill locally and stamps dbo-native identity

        Args:
            bill: Local Bill record to sync
            realm_id: QBO realm ID for API access

        Returns:
            QboBill: The local QboBill record created

        Raises:
            ValueError: If mapping lookup fails (vendor not mapped, etc.)
        """
        bill_id = coerce_id(bill.id)

        # Already-pushed short-circuit (dbo-native, verified)
        if bill.qbo_id:
            verified_qbo_id = verify_identity_dbo_only(
                bill, read_direct_by_qbo_identity=self.bill_service.read_by_qbo_identity,
            )
            if not verified_qbo_id:
                record_mapping_issue(
                    self.reconciliation_repo,
                    drift_type="bill_identity_conflict",
                    entity_type="Bill",
                    entity_public_id=str(bill.public_id) if bill.public_id else None,
                    qbo_id=bill.qbo_id,
                    realm_id=realm_id,
                    details=(
                        f"Push refused for Bill {bill_id}: dbo.Bill.QboId={bill.qbo_id!r} no "
                        f"longer resolves back to this Bill on a fresh dbo-only read (see "
                        f"verify_identity_dbo_only) — the identity was reassigned to a "
                        f"different Bill. Investigate which side is correct."
                    ),
                )
                raise ValueError(
                    f"Bill {bill_id} carries a QBO identity (qbo_id={bill.qbo_id!r}) that no "
                    f"longer resolves back to it on a fresh dbo-only read — refusing to push a "
                    f"possibly stolen/reassigned identity. See verify_identity_dbo_only."
                )
            existing_local_qbo_bill = self.qbo_bill_repo.read_by_qbo_id_and_realm_id(
                verified_qbo_id, realm_id
            )
            if existing_local_qbo_bill:
                logger.info(f"Bill {bill_id} is already mapped to QboBill {existing_local_qbo_bill.id}")
                return existing_local_qbo_bill
            # dbo.Bill carries a verified QboId but the local qbo.Bill staging cache has
            # no row for (verified_qbo_id, realm_id) — a genuine data-integrity anomaly
            # (the stamp and the staging-cache write happen together at the end of this
            # method), not the ordinary "never pushed" case. Refuse rather than risk
            # pushing a DUPLICATE Bill into QBO for an entity that already has one out
            # there.
            record_mapping_issue(
                self.reconciliation_repo,
                drift_type="bill_staging_row_missing",
                entity_type="Bill",
                entity_public_id=str(bill.public_id) if bill.public_id else None,
                qbo_id=str(verified_qbo_id),
                realm_id=realm_id,
                details=(
                    f"Push refused for Bill {bill_id}: dbo.Bill carries a verified QboId "
                    f"({verified_qbo_id!r}, realm_id={realm_id!r}) but no local qbo.Bill "
                    f"staging row exists for it. Investigate the missing staging row before "
                    f"retrying — pushing again risks creating a duplicate Bill in QBO."
                ),
            )
            raise ValueError(
                f"Bill {bill_id} carries a verified QBO identity (qbo_id={verified_qbo_id!r}, "
                f"realm_id={realm_id!r}) but no local qbo.Bill staging row exists for it — "
                f"refusing to push a possible duplicate. Investigate the missing staging row "
                f"before retrying."
            )

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
            qbo_line = self._build_qbo_line(line_item, idx, realm_id)
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
            logger.info(
                f"QboBill already stored locally for QboId {created_bill.id} "
                f"(retry after prior partial success) — reusing local record {existing_local_qbo_bill.id}"
            )
            local_qbo_bill = existing_local_qbo_bill
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

        # Stamp dbo-native identity — the sole identity store now (U-355)
        self.bill_service.repo.set_qbo_identity(
            id=bill_id,
            qbo_id=local_qbo_bill.qbo_id,
            realm_id=local_qbo_bill.realm_id or realm_id,
            sync_token=getattr(local_qbo_bill, "sync_token", None),
        )
        logger.info(f"Stamped dbo-native QBO identity: Bill {bill_id} <-> QboBill {local_qbo_bill.id}")

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

        U-355: resolves the target QboBill via dbo.Bill's own QboId (verified via
        verify_identity_dbo_only against a fresh dbo-only read), not the retired
        qbo.BillBill mapping hop — mirrors this connector's own sync_to_qbo_bill
        short-circuit.
        """
        from integrations.intuit.qbo.bill.external.schemas import QboBillUpdate

        bill = self.bill_service.read_by_id(bill_id)
        if not bill or not bill.qbo_id:
            logger.debug(f"No QBO identity for bill_id={bill_id}, skipping HasBeenBilled update")
            return

        verified_qbo_id = verify_identity_dbo_only(
            bill, read_direct_by_qbo_identity=self.bill_service.read_by_qbo_identity,
        )
        if not verified_qbo_id:
            logger.error(
                f"Bill {bill_id}'s dbo QboId no longer resolves back to it on a fresh "
                f"dbo-only read — refusing to push a HasBeenBilled update with a possibly "
                f"stolen/reassigned identity."
            )
            return

        # U-355 review fix: scope the staging lookup to bill.realm_id (the SAME realm
        # source verify_identity_dbo_only just checked against), not the bare `realm_id`
        # param — the two must agree, or a caller-supplied realm_id that ever diverges
        # from the bill's own stamped realm (single-realm today, so always equal, but
        # multi-realm activation is an already-booked future change) would let the verify
        # step pass while this lookup silently misses and returns early, looking like
        # "nothing to do" instead of a real mismatch.
        local_qbo_bill = self.qbo_bill_repo.read_by_qbo_id_and_realm_id(verified_qbo_id, bill.realm_id)
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
            qbo_line = self._build_qbo_line(line_item, seq + 1, realm_id)
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

        vendor = self.vendor_service.read_by_id(vendor_id)
        if not vendor:
            logger.warning(f"Vendor not found for id: {vendor_id}")
            return None

        # U-313: dbo.Vendor's native QboId is the SOLE identity store for
        # Vendor (Wave 5 "trust dbo alone" — qbo.VendorVendor no longer has a
        # writer, see docs/design/wave5.md). Still re-verified against a
        # fresh dbo-only read before trusting it for an outbound push — a
        # stale/"stolen" dbo QboId must never misroute a live Bill to the
        # wrong QBO vendor (mirrors U-276 round-4's push-side finding for
        # Project; see identity_consistency.py). No legacy hop left to fall
        # back to on a miss (removed; it had no data source left either).
        verified_qbo_id = verify_identity_dbo_only(
            vendor, read_direct_by_qbo_identity=self.vendor_service.read_by_qbo_identity,
        )
        if verified_qbo_id:
            return QboReferenceType(value=verified_qbo_id, name=vendor.name)

        logger.warning(f"No verified QBO identity for Vendor {vendor_id}")
        return None

    def _get_qbo_item_ref(self, sub_cost_code_id: int, realm_id: Optional[str] = None) -> Optional[QboReferenceType]:
        """
        Get QBO ItemRef from local sub_cost_code_id.

        U-307b: dbo-native SubCostCode.QboId direct via
        cost_code_resolver.resolve_qbo_item_ref -- no qbo.Item hop, realm-verified
        (see that module for the resolution/realm-matching contract) -- mirrors
        `_get_qbo_vendor_ref`/`_get_qbo_sales_term_ref`'s existing outbound-push
        identity verification.

        Args:
            sub_cost_code_id: Local SubCostCode database ID
            realm_id: QBO realm ID this push targets

        Returns:
            QboReferenceType with QBO item value and name, or None
        """
        item_ref = resolve_qbo_item_ref(
            sub_cost_code_id,
            realm_id,
            sub_cost_code_service=self.sub_cost_code_service,
        )
        if item_ref is None:
            logger.warning(f"No QBO Item mapping resolved for sub_cost_code_id: {sub_cost_code_id}")
            return None

        return QboReferenceType(value=item_ref.value, name=item_ref.name)

    def _get_qbo_customer_ref(self, project_id: int) -> Optional[QboReferenceType]:
        """
        Get QBO CustomerRef from local project_id.

        U-276 (Phase-4 pilot): reads dbo.Project.Name/.QboId directly (native
        since U-238a) instead of hopping qbo.CustomerProject -> qbo.Customer
        for DisplayName. Returns None if the Project has never been QBO-synced
        (no QboId stamped) — same "not mapped, don't push" contract as before.
        U-311 (Wave-5 Option A): the dbo identity is verified via
        `verify_identity_dbo_only` (a plain re-read of dbo.Project by its own
        (qbo_id, realm_id), trusted only when it still resolves back to this
        same row) — dbo-internal uniqueness alone doesn't guarantee the row
        wasn't reassigned between the read above and this call.

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

        verified_qbo_id = verify_identity_dbo_only(
            project,
            read_direct_by_qbo_identity=self.project_service.read_by_qbo_identity,
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

        Reads dbo.PaymentTerm's native QboId/Name (U-282) EXCLUSIVELY — the
        qbo.TermPaymentTerm -> qbo.Term legacy two-hop fallback was retired
        (U-352, the U-349 program's 3rd family: `TermPaymentTermConnector`
        went dbo-only, so there is no second store left to hop through). A
        dbo-native miss (no QboId stamped yet, a realm mismatch, or a
        transient dbo.PaymentTerm read failure) now returns None (no
        SalesTermRef on the push) instead of falling back. This is a
        deliberate PUSH-path behavior change on the miss branch, not a
        like-for-like port of the pre-U-352 shape — covered by its own
        regression test.

        `name` is sent to QBO as display-only text on SalesTermRef (QBO
        resolves the actual term link purely off `value`, the QboId) —
        dbo.PaymentTerm.Name, which a human can rename locally (accepted
        residual, same "never clobber curation" policy as
        preserve_human_edited_ref elsewhere in this package).

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
            return None

        if payment_term and payment_term.qbo_id and payment_term.realm_id == realm_id:
            return QboReferenceType(value=payment_term.qbo_id, name=payment_term.name)

        return None

    def _build_qbo_line(self, line_item, line_num: int, realm_id: Optional[str] = None) -> Optional[QboBillLineSchema]:
        """
        Build a QBO Bill line from a local BillLineItem.

        Args:
            line_item: BillLineItem record
            line_num: Line number
            realm_id: QBO realm ID this push targets

        Returns:
            QboBillLineSchema or None
        """
        logger.debug(f"Building QBO line for BillLineItem {line_item.id}: sub_cost_code_id={line_item.sub_cost_code_id}, project_id={line_item.project_id}")

        # Get QBO references — all line items must have valid mappings
        item_ref = None
        if line_item.sub_cost_code_id:
            item_ref = self._get_qbo_item_ref(line_item.sub_cost_code_id, realm_id)
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
                    f"has no QBO CustomerRef mapping. Ensure the Project has a QBO identity stamped "
                    f"(dbo.Project.QboId/RealmId, e.g. by syncing it from QBO) before syncing this bill."
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
