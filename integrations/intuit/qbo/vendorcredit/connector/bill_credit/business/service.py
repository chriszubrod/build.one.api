# Python Standard Library Imports
import logging
from dataclasses import dataclass
from decimal import Decimal
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
from integrations.intuit.qbo.base.reconciliation_recorder import (
    record_holding_code_on_completed_deduped,
    record_mapping_issue,
)
from integrations.intuit.qbo.base.field_ownership import preserve_human_edited_ref, qbo_ref_or_placeholder
from integrations.intuit.qbo.base.identity_consistency import verify_identity_dbo_only
from integrations.intuit.qbo.base.identity_fastpath import run_identity_fastpath_dbo_only
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from integrations.intuit.qbo.base.cost_code_resolver import qbo_lines_include_holding
from integrations.intuit.qbo.base.line_push import build_item_expense_line_plan, qbo_identity_stamp_landed
from integrations.intuit.qbo.base.push_refs import resolve_ap_account_ref, resolve_item_ref, resolve_verified_dbo_ref
from integrations.intuit.qbo.vendorcredit.external.client import QboVendorCreditClient
from integrations.intuit.qbo.vendorcredit.external.schemas import (
    QboItemBasedExpenseLineDetail,
    QboReferenceType,
    QboVendorCreditCreate,
    QboVendorCreditLine as QboVendorCreditLineSchema,
)
from entities.project.business.service import ProjectService
from entities.company.business.service import CompanyService
from entities.bill_credit_line_item_attachment.business.service import BillCreditLineItemAttachmentService
from entities.attachment.business.service import AttachmentService
from integrations.intuit.qbo.account.business.service import AP_ACCOUNT_TYPE, select_ap_account
from integrations.intuit.qbo.account.persistence.repo import QboAccountRepository
from integrations.intuit.qbo.base.errors import QboBudgetExceededError, QboDuplicateError, QboWriteRefusedError
from entities.sub_cost_code.business.service import SubCostCodeService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VendorCreditPush:
    """
    What the push vouches for: the QBO VendorCredit id (None when the header stamp did not land)
    and the credit's lines as read, for the attachment step.
    """

    qbo_id: Optional[str]
    line_items: list


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
        sub_cost_code_service: Optional[SubCostCodeService] = None,
        project_service: Optional[ProjectService] = None,
        company_service: Optional[CompanyService] = None,
        qbo_account_repo: Optional[QboAccountRepository] = None,
        bill_credit_line_item_attachment_service: Optional[BillCreditLineItemAttachmentService] = None,
        attachment_service: Optional[AttachmentService] = None,
        attachable_attachment_connector=None,
        vendor_credit_client_factory=None,
    ):
        self.bill_credit_service = bill_credit_service or BillCreditService()
        self.bill_credit_line_item_service = bill_credit_line_item_service or BillCreditLineItemService()
        self.vendor_service = vendor_service or VendorService()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()
        self.sub_cost_code_service = sub_cost_code_service
        # U-797c5a: push-side only (CustomerRef from the credit line's project).
        self.project_service = project_service or ProjectService()
        # U-797c5a round 1: AP ref (Bill parity), attachments (Bill parity), and client injection.
        self.company_service = company_service or CompanyService()
        self.qbo_account_repo = qbo_account_repo or QboAccountRepository()
        self.bill_credit_line_item_attachment_service = (
            bill_credit_line_item_attachment_service or BillCreditLineItemAttachmentService()
        )
        self.attachment_service = attachment_service or AttachmentService()
        self._attachable_attachment_connector = attachable_attachment_connector
        self._vendor_credit_client_factory = vendor_credit_client_factory or (
            lambda realm_id: QboVendorCreditClient(realm_id=realm_id)
        )
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
                    qbo_vc=qbo_vc,
                    vendor_public_id=vendor_public_id,
                    credit_number=credit_number,
                    qbo_lines=qbo_lines,
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
        if not bool(getattr(bill_credit, "is_draft", True)) and self._qbo_vendor_credit_lines_include_holding(
            qbo_lines, qbo_vc.realm_id
        ):
            record_holding_code_on_completed_deduped(
                self.reconciliation_repo,
                entity_type="BillCredit",
                entity_public_id=str(updated.public_id) if updated.public_id else None,
                qbo_id=str(qbo_vc.qbo_id) if qbo_vc.qbo_id else None,
                realm_id=qbo_vc.realm_id or "",
                details=(
                    f"QBO pull left completed BillCredit {updated.id} with at least one line "
                    f"resolved to suspense/holding sub cost code 00.02; status unchanged."
                ),
            )
        return updated

    def _qbo_vendor_credit_lines_include_holding(
        self, qbo_lines: List[QboVendorCreditLine], realm_id: Optional[str]
    ) -> bool:
        return qbo_lines_include_holding(
            qbo_lines,
            realm_id,
            sub_cost_code_service=self.sub_cost_code_service or SubCostCodeService(),
        )

    def _create_bill_credit(
        self,
        *,
        qbo_vc: QboVendorCredit,
        vendor_public_id: str,
        credit_number: str,
        qbo_lines: List[QboVendorCreditLine],
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
        born_draft = self._qbo_vendor_credit_lines_include_holding(qbo_lines, qbo_vc.realm_id)
        return self.bill_credit_service.create(
            vendor_public_id=vendor_public_id,
            credit_date=qbo_vc.txn_date,
            credit_number=credit_number,
            total_amount=to_decimal_or_none(qbo_vc.total_amt),
            memo=qbo_vc.private_note,
            is_draft=born_draft,
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
        #
        # U-361b: computed ONCE per credit (not per-line) — the dbo-only line fast
        # path's readopt step needs the full set of this pull's CURRENT QBO line
        # ids to tell a genuinely stale-identity orphan (safe to re-adopt) apart
        # from a line correctly bound elsewhere in this same credit (never steal).
        live_qbo_line_ids = frozenset(line.qbo_line_id for line in qbo_lines if line.qbo_line_id)
        failed = []
        for line in qbo_lines:
            try:
                self.line_item_connector.sync_from_qbo_line(
                    bill_credit_id, bill_credit_public_id, line, live_qbo_line_ids, realm_id
                )
            except Exception as e:
                logger.error(f"Error syncing line item {line.qbo_line_id}: {e}")
                failed.append((line.qbo_line_id, str(e)))
        if failed:
            raise RuntimeError(
                f"{len(failed)} of {len(qbo_lines)} credit line(s) failed to project for "
                f"bill_credit_id={bill_credit_id}: {failed}"
            )

    # ------------------------------------------------------------------ #
    # U-797c5a: BillCredit create push (mirrors BillBillConnector.sync_to_qbo_bill)
    # ------------------------------------------------------------------ #

    def sync_to_qbo_vendor_credit(self, bill_credit_id: int, realm_id: str) -> VendorCreditPush:
        """
        Create the QBO VendorCredit for a completed app-created BillCredit (outbox kind
        `sync_bill_credit_to_qbo`). Mirrors sync_to_qbo_bill:

        - A credit that already carries a qbo_id short-circuits after
          verify_identity_dbo_only and writes nothing (updates are U-797c5b).
        - Otherwise: vendor ref, line refs via the shared line helper (holding and
          unmapped lines fail the whole push), a negative computed amount is refused, create
          through the client (write gate applies in the HTTP layer), QboDuplicateError recorded
          and re-raised, stage the response through the pull's upsert, stamp each
          BillCreditLineItem by LineNum (a failed line stamp is recorded), then the header.
          A header stamp that raises is re-raised: the retry re-POSTs with the same RequestId
          (QBO dedups) and re-stamps.

        Returns VendorCreditPush: the QBO id this method can vouch for (None when the header
        stamp did not land) and the credit's lines as read, for the attachment step.
        Raises ValueError on a refused or unmappable credit; QBO errors propagate to the worker.
        """
        bill_credit = self.bill_credit_service.read_by_id(bill_credit_id)
        if not bill_credit:
            raise ValueError(f"BillCredit {bill_credit_id} not found for QBO push.")
        public_id = bill_credit.public_id

        if bill_credit.qbo_id:
            verified_qbo_id = verify_identity_dbo_only(
                bill_credit, read_direct_by_qbo_identity=self.bill_credit_service.read_by_qbo_identity,
            )
            if not verified_qbo_id:
                record_mapping_issue(
                    self.reconciliation_repo,
                    drift_type="bill_credit_identity_conflict",
                    entity_type="BillCredit",
                    entity_public_id=str(public_id) if public_id else None,
                    qbo_id=bill_credit.qbo_id,
                    realm_id=realm_id,
                    details=(
                        f"Push refused for BillCredit {bill_credit_id}: dbo.BillCredit.QboId="
                        f"{bill_credit.qbo_id!r} no longer resolves back to this credit on a fresh "
                        f"dbo-only read. Investigate which side is correct."
                    ),
                )
                raise ValueError(
                    f"BillCredit {bill_credit_id} carries a QBO identity (qbo_id={bill_credit.qbo_id!r}) "
                    f"that no longer resolves back to it — refusing to push. See verify_identity_dbo_only."
                )
            # Already in QBO: nothing to create. Coding updates are U-797c5b.
            return VendorCreditPush(
                qbo_id=verified_qbo_id,
                line_items=self.bill_credit_line_item_service.read_by_bill_credit_id(bill_credit_id=bill_credit_id),
            )

        if not bill_credit.credit_number:
            raise ValueError(
                f"BillCredit {bill_credit_id} has no credit_number. Set a credit number before syncing to QBO."
            )
        if not bill_credit.credit_date:
            raise ValueError(
                f"BillCredit {bill_credit_id} has no credit_date. Set a credit date before syncing to QBO."
            )

        qbo_vendor_ref = self._get_qbo_vendor_ref(bill_credit.vendor_id)
        if not qbo_vendor_ref:
            raise ValueError(f"No QBO vendor mapping found for vendor_id: {bill_credit.vendor_id}")

        credit_lines = self.bill_credit_line_item_service.read_by_bill_credit_id(bill_credit_id=bill_credit_id)
        if not credit_lines:
            raise ValueError("BillCredit has no line items. QBO requires at least one line item.")

        # All lines must map: a partial push is not allowed.
        qbo_lines = []
        line_num_to_line_item_id = {}
        for idx, line_item in enumerate(credit_lines, start=1):
            qbo_lines.append(self._build_qbo_line(line_item, idx, realm_id))
            line_num_to_line_item_id[idx] = line_item.id

        # Credits are stored positive. Guard the COMPUTED amount (qty x unit price when the stored
        # amount is absent), before any write.
        for qbo_line in qbo_lines:
            if qbo_line.amount is not None and qbo_line.amount < 0:
                raise ValueError(
                    f"BillCreditLineItem {line_num_to_line_item_id[qbo_line.line_num]} has a negative amount "
                    f"({qbo_line.amount}). Credits are stored positive; refusing to push a negative line to QBO."
                )

        payload = QboVendorCreditCreate(
            vendor_ref=qbo_vendor_ref,
            ap_account_ref=self._get_ap_account_ref(realm_id),
            txn_date=bill_credit.credit_date[:10],  # YYYY-MM-DD
            doc_number=bill_credit.credit_number,
            private_note=bill_credit.memo,
            line=qbo_lines,
        )
        logger.info(f"Creating VendorCredit in QBO for local BillCredit {bill_credit_id}: doc_number={bill_credit.credit_number}")

        with self._vendor_credit_client_factory(realm_id) as client:
            try:
                created = client.create_vendor_credit(payload)
            except QboDuplicateError:
                logger.warning(
                    f"BillCredit {bill_credit_id} doc_number={bill_credit.credit_number} already exists in QBO. "
                    f"Recording reconciliation issue and failing (no auto-adopt)."
                )
                record_mapping_issue(
                    self.reconciliation_repo,
                    drift_type="duplicate_qbo_vc_docnumber",
                    entity_type="BillCredit",
                    entity_public_id=str(public_id) if public_id else None,
                    qbo_id=None,
                    realm_id=realm_id,
                    details=(
                        f"Duplicate QBO VendorCredit DocNumber during push. Local BillCredit {bill_credit_id} "
                        f"(credit_number={bill_credit.credit_number}) collides with an existing QBO VendorCredit. "
                        f"Adopt-style recovery deliberately declined. Manual link required."
                    ),
                    severity="critical",
                )
                raise

        logger.info(f"Created QBO VendorCredit {created.id} with SyncToken {created.sync_token}")

        # Stage the created credit and its lines through the pull's own upsert.
        from integrations.intuit.qbo.vendorcredit.business.service import QboVendorCreditService

        QboVendorCreditService().upsert_from_external(created, realm_id)

        # Stamp each BillCreditLineItem by LineNum, then read back. A stamp that raises or does
        # not land is recorded (the line exists live in QBO); the push continues.
        line_repo = self.bill_credit_line_item_service.repo
        for qbo_line in created.line or []:
            if not qbo_line.line_num or qbo_line.line_num not in line_num_to_line_item_id:
                continue
            line_item_id = line_num_to_line_item_id[qbo_line.line_num]
            stamp_error = None
            restamped = None
            try:
                line_repo.set_qbo_identity(id=line_item_id, qbo_id=qbo_line.id, realm_id=realm_id)
                restamped = line_repo.read_by_id(line_item_id)
            except Exception as e:
                logger.error(f"Could not stamp BillCreditLineItem {line_item_id} QBO identity: {e}")
                stamp_error = e
            if stamp_error is None and qbo_identity_stamp_landed(restamped, qbo_line.id):
                continue
            if stamp_error is None:
                details = (
                    f"BillCreditLineItem {line_item_id} was just pushed to QBO as part of BillCredit "
                    f"{bill_credit_id} (QboVendorCreditLine {qbo_line.id}), but its dbo-native identity "
                    f"stamp did not land (realm_id={realm_id!r}). The line exists live in QBO but this "
                    f"row still reads QboId IS NULL. Needs manual stamping."
                )
            else:
                details = (
                    f"BillCreditLineItem {line_item_id} was pushed to QBO as QboVendorCreditLine {qbo_line.id} "
                    f"but set_qbo_identity raised: {stamp_error}. The line exists live in QBO with QboId IS NULL locally."
                )
            record_mapping_issue(
                self.reconciliation_repo,
                drift_type="bcli_line_push_stamp_failed",
                entity_type="BillCreditLineItem",
                entity_public_id=restamped.public_id if restamped and restamped.public_id else None,
                qbo_id=str(qbo_line.id),
                realm_id=realm_id or "",
                details=details,
            )

        # Stamp the header identity last, then verify it landed. A raise propagates (Bill's
        # behaviour): the retry re-POSTs with the same RequestId, QBO dedups, and the stamp is retried.
        header_repo = self.bill_credit_service.repo
        header_repo.set_qbo_identity(id=bill_credit_id, qbo_id=created.id, realm_id=realm_id)
        restamped_header = header_repo.read_by_id(bill_credit_id)
        if not qbo_identity_stamp_landed(restamped_header, created.id):
            record_mapping_issue(
                self.reconciliation_repo,
                drift_type="bill_credit_push_stamp_failed",
                entity_type="BillCredit",
                entity_public_id=str(public_id) if public_id else None,
                qbo_id=str(created.id),
                realm_id=realm_id,
                details=(
                    f"BillCredit {bill_credit_id} was just created in QBO as VendorCredit {created.id}, but its "
                    f"dbo-native identity stamp did not land (realm_id={realm_id!r}). The credit exists live in "
                    f"QBO but this row reads QboId IS NULL. Stamp it by hand, then re-queue the row: the create "
                    f"is skipped and the line attachments are pushed."
                ),
            )
            return VendorCreditPush(qbo_id=None, line_items=credit_lines)
        return VendorCreditPush(qbo_id=created.id, line_items=credit_lines)

    def _build_qbo_line(self, line_item, line_num: int, realm_id: Optional[str] = None) -> QboVendorCreditLineSchema:
        """Build one VendorCredit line via the shared helper (same decisions as a Bill line)."""
        plan = build_item_expense_line_plan(
            line_item,
            line_num,
            realm_id,
            resolve_item_ref=self._get_qbo_item_ref,
            resolve_customer_ref=self._get_qbo_customer_ref,
            unit_price_of=lambda li: li.unit_price,
            markup_of=lambda li: None,  # BillCreditLineItem carries no markup
            line_label="BillCreditLineItem",
            document_label="credit",
        )
        detail = QboItemBasedExpenseLineDetail(
            item_ref=plan.item_ref,
            customer_ref=plan.customer_ref,
            billable_status=plan.billable_status,
            qty=plan.qty,
            unit_price=plan.unit_price,
            markup_info=plan.markup_info,
        )
        return QboVendorCreditLineSchema(
            line_num=plan.line_num,
            description=plan.description,
            amount=plan.amount,
            detail_type="ItemBasedExpenseLineDetail",
            item_based_expense_line_detail=detail,
        )

    def _get_qbo_vendor_ref(self, vendor_id: Optional[int]) -> Optional[QboReferenceType]:
        """VendorRef from the credit's vendor (shared resolver, base/push_refs.py; same as Bill's push)."""
        ref = resolve_verified_dbo_ref(
            vendor_id,
            read_by_id=self.vendor_service.read_by_id,
            read_by_qbo_identity=self.vendor_service.read_by_qbo_identity,
            log=logger,
            not_found_msg="Vendor not found for id: {entity_id}",
            unverified_msg="No verified QBO identity for Vendor {entity_id}",
        )
        return QboReferenceType(value=ref[0], name=ref[1]) if ref else None

    def _get_qbo_item_ref(self, sub_cost_code_id: int, realm_id: Optional[str] = None) -> Optional[QboReferenceType]:
        """ItemRef from the SubCostCode (shared resolver; holding codes raise HoldingCodeRefused)."""
        ref = resolve_item_ref(
            sub_cost_code_id, realm_id, sub_cost_code_service=self.sub_cost_code_service, log=logger,
        )
        return QboReferenceType(value=ref[0], name=ref[1]) if ref else None

    def _get_qbo_customer_ref(self, project_id: Optional[int]) -> Optional[QboReferenceType]:
        """CustomerRef from the project's verified QboId (shared resolver; same contract as Bill's push)."""
        ref = resolve_verified_dbo_ref(
            project_id,
            read_by_id=self.project_service.read_by_id,
            read_by_qbo_identity=self.project_service.read_by_qbo_identity,
            require_qbo_id=True,
            log=logger,
            missing_qbo_id_msg="Project {entity_id} has no QBO identity (QboId) stamped",
        )
        return QboReferenceType(value=ref[0], name=ref[1]) if ref else None

    @property
    def attachable_attachment_connector(self):
        if self._attachable_attachment_connector is None:
            from integrations.intuit.qbo.attachable.connector.attachment.business.service import (
                AttachableAttachmentConnector,
            )
            self._attachable_attachment_connector = AttachableAttachmentConnector()
        return self._attachable_attachment_connector

    def _get_ap_account_ref(self, realm_id: str) -> Optional[QboReferenceType]:
        """AP account for the VendorCredit's APAccountRef (shared body, base/push_refs.py). Optional: QBO defaults it."""
        ref = resolve_ap_account_ref(
            realm_id, company_service=self.company_service, qbo_account_repo=self.qbo_account_repo, log=logger,
        )
        return QboReferenceType(value=ref[0], name=ref[1]) if ref else None

    def sync_attachments_to_qbo_vendor_credit(
        self, bill_credit_id: int, qbo_vendor_credit_id: str, realm_id: str, line_items=None,
    ) -> tuple:
        """
        Upload each line's attachment to the VendorCredit as an Attachable (Bill parity; the walk is
        shared in attachable/line_attachments.py). `line_items` is the push's already-read lines;
        when omitted they are read here. Returns (attachments_synced, errors).
        """
        from integrations.intuit.qbo.attachable.line_attachments import sync_line_attachments

        if line_items is None:
            line_items = self.bill_credit_line_item_service.read_by_bill_credit_id(bill_credit_id=bill_credit_id)
        return sync_line_attachments(
            line_items=line_items,
            read_link=lambda line_public_id: self.bill_credit_line_item_attachment_service.read_by_bill_credit_line_item_id(
                line_public_id
            ),
            read_attachment=lambda attachment_id: self.attachment_service.read_by_id(id=attachment_id),
            connector=self.attachable_attachment_connector,
            entity_type="VendorCredit",
            qbo_entity_id=qbo_vendor_credit_id,
            realm_id=realm_id,
            logger=logger,
            label=f"BillCredit {bill_credit_id}",
        )

    # One of FIVE near-identical dbo-first/legacy-fallback vendor-ref resolvers
    # (U-284v): this one, BillBillConnector._get_vendor_public_id (pull) +
    # _get_qbo_vendor_ref (push), PurchaseExpenseConnector._get_vendor_public_id,
    # CodingSuggestionService._resolve_vendor_id. Hand-copied deliberately,
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
