# Python Standard Library Imports
import logging
from typing import List, Optional

# Third-party Imports

# Local Imports
from shared.api.money import to_decimal_or_none
from integrations.intuit.qbo.vendorcredit.business.model import QboVendorCredit, QboVendorCreditLine
from integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.business.service import (
    VendorCreditLineItemConnector,
)
from entities.bill_credit.business.service import BillCreditService
from entities.bill_credit.business.model import BillCredit
from entities.bill_credit_line_item.business.service import BillCreditLineItemService
from entities.vendor.business.service import VendorService
from integrations.intuit.qbo.base.pull_race import guard_lines_present
from integrations.intuit.qbo.base.compensation import rollback_orphan_header
from integrations.intuit.qbo.base.reconciliation_recorder import record_mapping_issue
from integrations.intuit.qbo.base.field_ownership import preserve_human_edited_ref, qbo_ref_or_placeholder
from integrations.intuit.qbo.base.identity_consistency import verify_identity_dbo_only
from integrations.intuit.qbo.base.identity_fastpath import run_identity_fastpath_dbo_only
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository

logger = logging.getLogger(__name__)


class VendorCreditBillCreditConnector:
    """
    Connector service for syncing QBO VendorCredits to BillCredits.

    U-353: dbo-only identity resolution via `run_identity_fastpath_dbo_only` --
    no `qbo.VendorCreditBillCredit` mapping-table read/write of any kind (U-349
    program family 4, mirrors U-350's `CompanyInfoCompanyConnector` / U-310's
    `CustomerCustomerConnector` / U-313's `VendorVendorConnector`, per Wave 5's
    "trust dbo alone" plan, `docs/design/u349-qbo-mapping-table-retirement.md`).
    `dbo.BillCredit.QboId`/`RealmId` (U-278) is the sole identity store;
    dbo.BillCredit's own filtered unique index (`UQ_BillCredit_QboId_RealmId`)
    + `SetBillCreditQboIdentity`'s theft-clear UPDATE guarantee at most one row
    holds a given identity at any instant, so a direct hit needs no
    cross-check and the old heal/adopt-by-fingerprint branch structure (driven
    by a second, independently-writable mapping table) no longer has anything
    to drift from. Unlike U-350 (Company, name-matched master data),
    BillCredit is a transactional document with no natural pre-identity dedup
    target, so a genuine miss simply creates a new BillCredit -- protected at
    the DB layer by `UQ_BillCredit_VendorId_CreditNumber`, exactly as the
    pre-U-353 legacy CREATE path already was. No push path exists for this
    connector (pull-only, confirmed at Gate-1 -- nothing dead to remove).
    """

    def __init__(
        self,
        bill_credit_service: Optional[BillCreditService] = None,
        bill_credit_line_item_service: Optional[BillCreditLineItemService] = None,
        vendor_service: Optional[VendorService] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
        line_item_connector: Optional[VendorCreditLineItemConnector] = None,
    ):
        self.bill_credit_service = bill_credit_service or BillCreditService()
        self.bill_credit_line_item_service = bill_credit_line_item_service or BillCreditLineItemService()
        self.vendor_service = vendor_service or VendorService()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()
        # Run-scoped (U-229): built once here and reused across every credit processed by
        # this connector instance, so VendorCreditLineItemConnector._sub_cost_code_cache
        # persists for the whole pull run instead of resetting per credit. Mirrors
        # PurchaseExpenseConnector's identical hoist of PurchaseLineExpenseLineItemConnector.
        self.line_item_connector = line_item_connector or VendorCreditLineItemConnector()

    def sync_from_qbo_vendor_credit(
        self,
        qbo_vc: QboVendorCredit,
        qbo_lines: List[QboVendorCreditLine],
    ) -> Optional[BillCredit]:
        """
        Sync a QBO VendorCredit to BillCredit module, via the dbo-only identity
        fast path (U-353).

        Process:
        1. Resolve vendor via VendorRefValue -> QboVendor -> Vendor (dbo-native)
        2. Direct dbo.BillCredit.QboId/RealmId hit -> update in place
        3. Genuine miss -> create a new BillCredit, stamp identity, sync lines
        """
        # Last-resort guard against the QBO pull-race that mints half-built credits (see
        # base.pull_race). Placed BEFORE the try so the RuntimeError isn't swallowed by the
        # inner except. Pull scripts pre-read past the race; this protects every other caller.
        guard_lines_present(
            qbo_lines, qbo_vc.total_amt,
            entity_label="QboVendorCredit", entity_id=qbo_vc.id, qbo_id=qbo_vc.qbo_id,
        )

        try:
            vendor_public_id = self._get_vendor_public_id(qbo_vc.vendor_ref_value, qbo_vc.realm_id)
            if not vendor_public_id:
                # Permanent data issue — raise (don't silently return None) so the
                # caller classifies it as a skip that doesn't block the watermark,
                # consistent with the Bill and Purchase connectors.
                raise ValueError(
                    f"No vendor mapping found for QBO vendor ref: {qbo_vc.vendor_ref_value}"
                )

            # QBO-derived credit number (real DocNumber or the QBO-<id> placeholder).
            # Hoisted once and reused on both the UPDATE and CREATE paths, mirroring the
            # Bill/Expense siblings.
            credit_number = qbo_ref_or_placeholder(qbo_vc.doc_number, qbo_vc.qbo_id)

            outcome = run_identity_fastpath_dbo_only(
                qbo_id=qbo_vc.qbo_id,
                realm_id=qbo_vc.realm_id,
                entity_label="BillCredit",
                external_label="QboVendorCredit",
                lock_resource_label="BillCredit",
                read_direct_by_qbo_identity=self.bill_credit_service.read_by_qbo_identity,
                apply_fields=lambda entity: self._apply_bill_credit_fields_and_sync(
                    entity,
                    qbo_vc=qbo_vc,
                    vendor_public_id=vendor_public_id,
                    credit_number=credit_number,
                    qbo_lines=qbo_lines,
                ),
                resolve_candidate=lambda: self._create_bill_credit(
                    qbo_vc=qbo_vc, vendor_public_id=vendor_public_id, credit_number=credit_number,
                ),
                stamp_identity=lambda candidate: self._stamp_bill_credit_identity(
                    candidate, qbo_vc=qbo_vc, qbo_lines=qbo_lines,
                ),
            )
            if outcome.entity is None:
                # No longer race-reachable in practice (see run_identity_fastpath_
                # dbo_only's Raises docstring) — kept as a backstop for a directly
                # invoked falsy qbo_vc.qbo_id, mirroring every sibling connector's
                # identical guard (U-350/U-310/U-313/U-311).
                raise RuntimeError(
                    f"Failed to resolve BillCredit for QboVendorCredit {qbo_vc.id} "
                    f"(qbo_id={qbo_vc.qbo_id}) via the dbo-only identity fast path"
                )
            return outcome.entity

        except ValueError:
            # Permanent data issue — propagate for the caller to classify as a skip.
            raise
        except Exception as e:
            # Transient error (DB, connection, etc.) — propagate so the caller can
            # block the watermark and retry next run, instead of silently dropping it.
            logger.error(f"Error syncing VendorCredit {qbo_vc.qbo_id} to BillCredit: {e}")
            raise

    def _apply_bill_credit_fields_and_sync(
        self,
        bill_credit: BillCredit,
        *,
        qbo_vc: QboVendorCredit,
        vendor_public_id: str,
        credit_number: str,
        qbo_lines: List[QboVendorCreditLine],
    ) -> Optional[BillCredit]:
        """
        `apply_fields` for the dbo-only fast path's HIT branch (U-353): write the
        QboVendorCredit-derived fields onto an existing dbo-identity-matched
        BillCredit, persist, and sync its line items. Covers both a plain direct
        hit and a race-resolved hit (run_identity_fastpath_dbo_only calls this
        same callback for both — see that function's docstring).

        Deliberately does NOT stamp dbo-native identity — the row already carries
        it by construction (that's how `read_by_qbo_identity` found it in the
        first place; re-stamping here would be a wasted round trip on the
        steady-state path this whole feature exists to keep cheap). Mirrors
        `CompanyInfoCompanyConnector._apply_company_fields_and_sync` (U-350).

        Returns None on a ROWVERSION-race/concurrent-delete `update_by_public_id`
        miss (U-291) — `run_identity_fastpath_dbo_only`'s own `_apply()` raises
        `raise_concurrent_write_race` unconditionally whenever `apply_fields`
        returns None, so this method staying silent on a miss (and skipping line
        sync) is what keeps that single raise as the ONE place the guarantee lives.

        U-027 (rule of three): never clobber a human-corrected credit_number on
        re-pull. Preserve the stored value unless it is empty/null or the
        QBO-<id> placeholder (which still upgrades to a real doc_number). The
        CREATE path is unchanged. See base.field_ownership. ACCEPTED RESIDUAL:
        same as the Bill sibling — a preserved credit_number diverges from the
        QBO number, so IF this credit's mapping is later lost while it persists
        (structurally no longer possible post-U-353 — dbo identity has no
        second store to lose), the CREATE path's UQ_BillCredit_VendorId_
        CreditNumber dedup keys on the QBO number and won't match -> possible
        duplicate. Retained here as historical context only.
        """
        effective_credit_number = preserve_human_edited_ref(
            bill_credit.credit_number, credit_number, qbo_vc.qbo_id
        )
        updated = self.bill_credit_service.update_by_public_id(
            public_id=bill_credit.public_id,
            row_version=bill_credit.row_version,
            vendor_public_id=vendor_public_id,
            credit_date=qbo_vc.txn_date,
            credit_number=effective_credit_number,
            total_amount=to_decimal_or_none(qbo_vc.total_amt),
            memo=qbo_vc.private_note,
        )
        if updated is None:
            return None
        self._sync_line_items(updated.id, updated.public_id, qbo_lines, qbo_vc.realm_id)
        return updated

    def _create_bill_credit(
        self, *, qbo_vc: QboVendorCredit, vendor_public_id: str, credit_number: str,
    ) -> Optional[BillCredit]:
        """
        `resolve_candidate` for the dbo-only fast path's MISS branch (U-353):
        called only under `run_identity_fastpath_dbo_only`'s create lock, once a
        genuine miss is confirmed (no dbo.BillCredit currently holds this
        identity, including the re-read under lock). Unlike Company's
        by-name-match candidate resolution, BillCredit is a transactional
        document — there is no natural existing-row lookup to attempt first;
        this mirrors the pre-U-353 legacy CREATE step exactly, protected by
        `UQ_BillCredit_VendorId_CreditNumber` at the DB layer.
        """
        logger.info(f"No existing BillCredit found. Creating new BillCredit from QboVendorCredit {qbo_vc.id}")
        return self.bill_credit_service.create(
            vendor_public_id=vendor_public_id,
            credit_date=qbo_vc.txn_date,
            credit_number=credit_number,
            total_amount=to_decimal_or_none(qbo_vc.total_amt),
            memo=qbo_vc.private_note,
            is_draft=False,
        )

    def _stamp_bill_credit_identity(
        self, candidate: Optional[BillCredit], *, qbo_vc: QboVendorCredit, qbo_lines: List[QboVendorCreditLine],
    ) -> Optional[BillCredit]:
        """
        `stamp_identity` for the dbo-only fast path's MISS branch (U-353): stamp
        dbo-native identity onto the just-created BillCredit, then sync its line
        items. `candidate` is a fresh, uniquely-ours row from `_create_bill_credit`
        (not a side-channel-key match shared with any other incoming QBO record),
        so — unlike Company's by-name candidate — there is no concurrent-different-
        qbo_id race to guard with `stamp_dbo_identity_with_lock`'s extra lock;
        `run_identity_fastpath_dbo_only`'s own create lock already serializes two
        syncs of the SAME QboVendorCredit against each other.

        On a permanent line-sync failure, best-effort deletes the just-created
        header via `rollback_orphan_header` so a bad create never strands a
        header-only zombie (mirrors the pre-U-353 legacy CREATE path's
        compensating rollback) — `delete_mapping` is a no-op here, since there is
        no mapping row left to delete; the shared helper's mapping-then-header
        delete ORDER is dead weight for this family now but the parameter itself
        stays required (PurchaseExpense, family 5, still has a real mapping to
        delete via this exact helper — see `base/compensation.py`).

        Re-reads and returns the row after stamping (code-review Angle D, round
        2): `set_qbo_identity` is a void DB write that never mutates `candidate`
        in memory, so returning `candidate` as-is would hand the caller a
        BillCredit whose `qbo_id`/`realm_id` still read as their pre-stamp
        `None` even though the DB row is correctly stamped — every sibling
        dbo-only connector gets this freshness guarantee for free from
        `stamp_dbo_identity_with_lock`'s own re-read; this family's simpler
        shape (no side-channel-key race, so that helper's second lock isn't
        needed — see above) still owes the caller the same freshness.
        """
        if candidate is None:
            return None

        self.bill_credit_service.repo.set_qbo_identity(
            id=coerce_id(candidate.id), qbo_id=qbo_vc.qbo_id, realm_id=qbo_vc.realm_id,
        )

        try:
            self._sync_line_items(candidate.id, candidate.public_id, qbo_lines, qbo_vc.realm_id)
        except Exception:
            rollback_orphan_header(
                delete_header=lambda: self.bill_credit_service.delete_by_public_id(candidate.public_id),
                delete_mapping=lambda: None,
                entity_label='BillCredit', entity_id=candidate.id,
                on_header_delete_failed=lambda exc: self._record_orphan_header_issue(
                    bill_credit=candidate, qbo_vc=qbo_vc, exc=exc
                ),
            )
            raise

        logger.info(f"Created BillCredit {candidate.public_id} from VendorCredit {qbo_vc.qbo_id}")
        return self.bill_credit_service.read_by_id(candidate.id)

    def _record_orphan_header_issue(
        self,
        *,
        bill_credit: BillCredit,
        qbo_vc: QboVendorCredit,
        exc: Exception,
    ) -> None:
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="orphan_billcredit_header",
            entity_type="BillCredit",
            entity_public_id=str(bill_credit.public_id) if bill_credit.public_id else None,
            qbo_id=str(qbo_vc.qbo_id) if qbo_vc.qbo_id else None,
            realm_id=qbo_vc.realm_id or "",
            details=(
                f"Compensating rollback failed to delete orphan BillCredit {bill_credit.id} "
                f"({bill_credit.public_id}): {exc}. Header blocks re-pull until manually resolved."
            ),
        )

    def _sync_line_items(
        self,
        bill_credit_id: int,
        bill_credit_public_id: str,
        qbo_lines: List[QboVendorCreditLine],
        realm_id: Optional[str] = None,
    ) -> None:
        """
        Sync line items from QBO VendorCredit to BillCreditLineItems by UPSERTING
        each line in place (parity with Bill's _sync_line_items).

        Each BillCreditLineItem is matched by its own dbo-native, parent-scoped
        (BillCreditId, QboId) identity (U-361 — the VendorCreditLineItemBillCredit
        LineItem mapping table is retired) and updated in place rather than
        deleted+recreated. This preserves the BillCreditLineItem PK, its attachments,
        and any InvoiceLineItem -> credit-line FK, and removes the old duplication
        vector entirely (an invoice-referenced line is updated, never re-created).
        Stale-line cleanup (lines QBO removed) is handled in the snapshot layer.
        """
        # Upsert each QBO line in place. No delete-then-recreate: stale-line cleanup
        # lives in the snapshot layer, and the connector matches existing
        # BillCreditLineItems by dbo-native (BillCreditId, QboId) identity via the
        # shared run_line_identity_fastpath_dbo_only primitive.
        # Attempt EVERY line, collect failures, then RAISE if any failed — never leave
        # a BillCredit whose header total doesn't match its lines. Raising marks the
        # whole credit failed so the pull watermark holds and it retries (idempotent).
        failed = []
        for line in qbo_lines:
            try:
                self.line_item_connector.sync_from_qbo_line(bill_credit_id, bill_credit_public_id, line, realm_id)
            except Exception as e:
                logger.error(f"Error syncing line item {line.qbo_line_id}: {e}")
                failed.append((line.qbo_line_id, str(e)))
        if failed:
            raise RuntimeError(
                f"{len(failed)} of {len(qbo_lines)} credit line(s) failed to project for "
                f"bill_credit_id={bill_credit_id}: {failed}"
            )

    # One of FIVE near-identical dbo-first/legacy-fallback vendor-ref resolvers
    # (U-284v): this one, BillBillConnector._get_vendor_public_id (pull) +
    # _get_qbo_vendor_ref (push), PurchaseExpenseConnector._get_vendor_public_id,
    # ExpenseCodingItemService._resolve_vendor_id. Hand-copied deliberately,
    # mirroring _get_project_public_id's own precedent — see TODO.md's
    # U-005[reuse] entry before adding a 6th copy or consolidating.
    def _get_vendor_public_id(self, qbo_vendor_ref_value: Optional[str], realm_id: Optional[str] = None) -> Optional[str]:
        """Resolve QBO vendor ref (QBO API string ID) to local Vendor public_id.

        U-313: dbo.Vendor's native QboId/RealmId is the SOLE identity store
        for Vendor (Wave 5 "trust dbo alone" — qbo.VendorVendor no longer has
        a writer, see docs/design/wave5.md). No legacy hop left to fall back
        to on a miss (removed; it had no data source left either).
        """
        if not qbo_vendor_ref_value:
            return None

        try:
            direct_vendor = self.vendor_service.read_by_qbo_identity(qbo_vendor_ref_value, realm_id)
            if direct_vendor:
                verified_qbo_id = verify_identity_dbo_only(
                    direct_vendor,
                    read_direct_by_qbo_identity=self.vendor_service.read_by_qbo_identity,
                )
                if verified_qbo_id:
                    return direct_vendor.public_id

            return None
        except Exception as e:
            logger.warning(f"Error resolving vendor ref {qbo_vendor_ref_value}: {e}")
            return None
