# Python Standard Library Imports
import logging
from typing import Dict, List, Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.purchase.business.model import QboPurchase, QboPurchaseLine
from integrations.intuit.qbo.vendor.persistence.repo import QboVendorRepository
from entities.expense.business.service import ExpenseService
from entities.expense.business.model import Expense
from entities.expense_line_item.business.service import ExpenseLineItemService
from entities.vendor.business.service import VendorService
from integrations.intuit.qbo.base.pull_race import guard_lines_present
from integrations.intuit.qbo.base.compensation import rollback_orphan_header
from integrations.intuit.qbo.base.identity_consistency import verify_identity_dbo_only
from integrations.intuit.qbo.base.field_ownership import preserve_human_edited_ref, qbo_ref_or_placeholder
from integrations.intuit.qbo.base.identity_fastpath import run_identity_fastpath_dbo_only
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.reconciliation_recorder import record_mapping_issue
from integrations.intuit.qbo.base.cost_code_resolver import resolve_qbo_item_ref
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from entities.sub_cost_code.business.service import SubCostCodeService

logger = logging.getLogger(__name__)


class PurchaseExpenseConnector:
    """
    Connector service for synchronization between QboPurchase and Expense modules.

    U-354: dbo-only identity resolution via `run_identity_fastpath_dbo_only` --
    no `qbo.PurchaseExpense` mapping-table read/write of any kind (U-349 program
    family 5, mirrors U-350's `CompanyInfoCompanyConnector` / U-353's
    `VendorCreditBillCreditConnector`, per Wave 5's "trust dbo alone" plan,
    `docs/design/u349-qbo-mapping-table-retirement.md`). `dbo.Expense.QboId`/
    `RealmId` (U-238a) is the sole identity store; dbo.Expense's own filtered
    unique index + `SetExpenseQboIdentity`'s theft-clear UPDATE guarantee at
    most one row holds a given identity at any instant, so a direct hit needs
    no cross-check and the old heal/adopt-by-fingerprint branch structure
    (driven by a second, independently-writable mapping table) no longer has
    anything to drift from. Like VendorCredit (U-353), Expense is a
    transactional document with no natural pre-identity dedup target, so a
    genuine miss simply creates a new Expense -- no mapping-table constraint
    protected the old CREATE path either (Expense carries no analogous unique
    business key). The surgical `recode_purchase_line` cockpit path (below)
    round-trips raw QBO JSON directly and never touched the mapping table --
    untouched by this unit, per Chris's 2026-08-20 decision.
    """

    def __init__(
        self,
        expense_service: Optional[ExpenseService] = None,
        vendor_service: Optional[VendorService] = None,
        vendor_vendor_repo=None,
        qbo_vendor_repo: Optional[QboVendorRepository] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
        sub_cost_code_service: Optional[SubCostCodeService] = None,
    ):
        """Initialize the PurchaseExpenseConnector."""
        self.expense_service = expense_service or ExpenseService()
        self.vendor_service = vendor_service or VendorService()
        # U-313: no longer read anywhere in this file (_get_vendor_public_id
        # moved fully dbo-only, no qbo.VendorVendor hop left). U-314 dropped
        # qbo.VendorVendor entirely, so vendor_vendor_repo can no longer
        # default-construct its old repo class -- kept as an untyped,
        # unconstructed constructor param rather than removed, so the ~10
        # existing test call sites across other units that still pass
        # vendor_vendor_repo=/qbo_vendor_repo= don't need to change for a
        # unit whose real scope is the Vendor mapping table, not this
        # connector's constructor — see TODO.md's U-313 follow-ups.
        self.vendor_vendor_repo = vendor_vendor_repo
        self.qbo_vendor_repo = qbo_vendor_repo or QboVendorRepository()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()
        # U-307b: only ever passed to cost_code_resolver.resolve_qbo_item_ref, never
        # used directly here. Kept as an injectable constructor param (not defaulted
        # inline) so tests can inject a fake exactly as they did before this repoint.
        self.sub_cost_code_service = sub_cost_code_service
        # Per-sync cache: avoids 3 DB round-trips per purchase when multiple purchases
        # share the same QBO vendor (the common case).
        self._vendor_cache: dict = {}
        # Single line connector shared across all purchases so _sub_cost_code_cache and
        # _project_cache persist for the entire sync run, not just per-purchase.
        from integrations.intuit.qbo.purchase.connector.expense_line_item.business.service import PurchaseLineExpenseLineItemConnector
        self._line_connector = PurchaseLineExpenseLineItemConnector()

    def sync_from_qbo_purchase(self, qbo_purchase: QboPurchase, qbo_purchase_lines: List[QboPurchaseLine]) -> Expense:
        """
        Sync a QBO Purchase to Expense module, via the dbo-only identity fast
        path (U-354).

        Process:
        1. Resolve vendor via EntityRefValue -> QboVendor -> Vendor (dbo-native)
        2. Direct dbo.Expense.QboId/RealmId hit -> update in place
        3. Genuine miss -> create a new Expense, stamp identity, sync lines
        """
        # Find vendor mapping to get Vendor public_id
        # Purchase uses EntityRef instead of VendorRef
        vendor_public_id = self._get_vendor_public_id(qbo_purchase.entity_ref_value, qbo_purchase.realm_id)
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

        def _apply_expense_fields(direct: Expense) -> Optional[Expense]:
            """
            `apply_fields` for the dbo-only fast path's HIT branch (U-354): write
            the QBO-derived fields onto an existing dbo-identity-matched Expense,
            persist, and sync its line items. Covers both a plain direct hit and
            a race-resolved hit (run_identity_fastpath_dbo_only calls this same
            callback for both).

            KI-42 / U-024 (rule of three): never silently revert a human-corrected
            reference_number on re-pull. The shared base helper keeps the stored
            value unless it is empty/null or the QBO-<id> placeholder (which still
            upgrades to a real doc_number when one appears). See base.field_ownership.

            Unlike Company/Address/Project/VendorCredit, Expense (like Bill) carries
            SyncToken as part of its identity -- this re-stamp is NOT skipped on a
            plain HIT: it refreshes SyncToken on every pull, matching the pre-U-354
            behavior exactly.

            Returns None on a ROWVERSION-race/concurrent-delete `update_by_public_id`
            miss (U-291) -- `run_identity_fastpath_dbo_only`'s own `_apply()` raises
            `raise_concurrent_write_race` unconditionally whenever `apply_fields`
            returns None, so this method staying silent on a miss (and skipping line
            sync) is what keeps that single raise as the ONE place the guarantee lives.
            """
            effective_ref = preserve_human_edited_ref(
                direct.reference_number, reference_number, qbo_purchase.qbo_id
            )
            updated = self.expense_service.update_by_public_id(
                direct.public_id,
                row_version=direct.row_version,
                vendor_public_id=vendor_public_id,
                expense_date=expense_date,
                reference_number=effective_ref,
                total_amount=total_amount,
                memo=memo,
                is_draft=False,
                is_credit=qbo_purchase.credit or False,
            )
            if updated is None:
                return None
            expense_id = coerce_id(updated.id)
            self.expense_service.repo.set_qbo_identity(
                id=expense_id,
                qbo_id=qbo_purchase.qbo_id,
                realm_id=qbo_purchase.realm_id,
                sync_token=getattr(qbo_purchase, "sync_token", None),
            )
            self._sync_line_items(expense_id, updated.public_id, qbo_purchase_lines, qbo_purchase.realm_id)
            return updated

        outcome = run_identity_fastpath_dbo_only(
            qbo_id=qbo_purchase.qbo_id,
            realm_id=qbo_purchase.realm_id,
            entity_label="Expense",
            external_label="QboPurchase",
            lock_resource_label="Expense",
            read_direct_by_qbo_identity=self.expense_service.read_by_qbo_identity,
            apply_fields=_apply_expense_fields,
            resolve_candidate=lambda: self._create_expense(
                qbo_purchase=qbo_purchase, vendor_public_id=vendor_public_id, reference_number=reference_number,
            ),
            stamp_identity=lambda candidate: self._stamp_expense_identity(
                candidate, qbo_purchase=qbo_purchase, qbo_purchase_lines=qbo_purchase_lines,
            ),
        )
        if outcome.entity is None:
            # No longer race-reachable in practice (see run_identity_fastpath_
            # dbo_only's Raises docstring) — kept as a backstop for a directly
            # invoked falsy qbo_purchase.qbo_id, mirroring every sibling
            # connector's identical guard (U-350/U-353/U-310/U-313).
            raise RuntimeError(
                f"Failed to resolve Expense for QboPurchase {qbo_purchase.id} "
                f"(qbo_id={qbo_purchase.qbo_id}) via the dbo-only identity fast path"
            )
        return outcome.entity

    def _create_expense(
        self, *, qbo_purchase: QboPurchase, vendor_public_id: str, reference_number: str,
    ) -> Optional[Expense]:
        """
        `resolve_candidate` for the dbo-only fast path's MISS branch (U-354):
        called only under `run_identity_fastpath_dbo_only`'s create lock, once a
        genuine miss is confirmed (no dbo.Expense currently holds this
        identity, including the re-read under lock). Expense carries no
        analogous unique business key to dedup against, so this mirrors the
        pre-U-354 legacy CREATE step exactly.
        """
        logger.info(f"Creating new Expense from QboPurchase {qbo_purchase.id}: reference_number={reference_number}")
        return self.expense_service.create(
            vendor_public_id=vendor_public_id,
            expense_date=qbo_purchase.txn_date,
            reference_number=reference_number,
            total_amount=qbo_purchase.total_amt,
            memo=qbo_purchase.private_note,
            is_draft=False,
            is_credit=qbo_purchase.credit or False,
        )

    def _stamp_expense_identity(
        self, candidate: Optional[Expense], *, qbo_purchase: QboPurchase, qbo_purchase_lines: List[QboPurchaseLine],
    ) -> Optional[Expense]:
        """
        `stamp_identity` for the dbo-only fast path's MISS branch (U-354): stamp
        dbo-native identity onto the just-created Expense, then sync its line
        items. `candidate` is a fresh, uniquely-ours row from `_create_expense`
        (not a side-channel-key match shared with any other incoming QBO
        record), so — unlike Company's by-name candidate — there is no
        concurrent-different-qbo_id race to guard with extra locking;
        `run_identity_fastpath_dbo_only`'s own create lock already serializes
        two syncs of the SAME QboPurchase against each other.

        On a permanent failure in EITHER the identity stamp or the line sync,
        best-effort deletes the just-created header via `rollback_orphan_header`
        so a bad create never strands a header-only zombie (mirrors the
        pre-U-354 legacy CREATE path's compensating rollback, which wrapped
        its own identity-stamp call in the same try/except as the mapping
        insert — see `create_mapping`'s retired docstring: "Stamp dbo-native
        identity FIRST — if this fails, nothing else has been created yet, so
        the caller's existing rollback... fully cleans up") — `delete_mapping`
        is a no-op here, since there is no mapping row left to delete. Both
        steps share ONE try/except (not two) so a `set_qbo_identity` failure
        gets the exact same cleanup as a line-sync failure — otherwise a
        transient stamp failure would leave an unstamped orphan Expense that
        `read_direct_by_qbo_identity` can never find again (it carries no
        QboId), and the next pull tick would mint a genuine duplicate.

        Re-reads and returns the row after stamping (mirrors
        `VendorCreditBillCreditConnector._stamp_bill_credit_identity`,
        U-353): `set_qbo_identity` is a void DB write that never mutates
        `candidate` in memory, so returning `candidate` as-is would hand the
        caller an Expense whose `qbo_id`/`realm_id` still read as their
        pre-stamp `None` even though the DB row is correctly stamped.
        """
        if candidate is None:
            return None

        expense_id = coerce_id(candidate.id)
        try:
            self.expense_service.repo.set_qbo_identity(
                id=expense_id,
                qbo_id=qbo_purchase.qbo_id,
                realm_id=qbo_purchase.realm_id,
                sync_token=getattr(qbo_purchase, "sync_token", None),
            )
            self._sync_line_items(expense_id, candidate.public_id, qbo_purchase_lines, qbo_purchase.realm_id)
        except Exception:
            rollback_orphan_header(
                delete_header=lambda: self.expense_service.delete_by_public_id(candidate.public_id),
                delete_mapping=lambda: None,
                entity_label='Expense', entity_id=expense_id,
                on_header_delete_failed=lambda exc: self._record_orphan_header_issue(
                    expense=candidate, qbo_purchase=qbo_purchase, exc=exc
                ),
            )
            raise

        return self.expense_service.read_by_id(expense_id)

    def _record_orphan_header_issue(
        self,
        *,
        expense: Expense,
        qbo_purchase: QboPurchase,
        exc: Exception,
    ) -> None:
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="orphan_expense_header",
            entity_type="Expense",
            entity_public_id=str(expense.public_id) if expense.public_id else None,
            qbo_id=str(qbo_purchase.qbo_id) if qbo_purchase.qbo_id else None,
            realm_id=qbo_purchase.realm_id or "",
            details=(
                f"Compensating rollback failed to delete orphan Expense {expense.id} "
                f"({expense.public_id}): {exc}. Header blocks re-pull until manually resolved."
            ),
        )

    # One of FIVE near-identical dbo-first/legacy-fallback vendor-ref resolvers
    # (U-284v): this one, BillBillConnector._get_vendor_public_id (pull) +
    # _get_qbo_vendor_ref (push), VendorCreditBillCreditConnector
    # ._get_vendor_public_id, ExpenseCodingItemService._resolve_vendor_id.
    # Hand-copied deliberately, mirroring _get_project_public_id's own
    # precedent — see TODO.md's U-005[reuse] entry before adding a 6th copy
    # or consolidating.
    def _get_vendor_public_id(self, qbo_entity_ref_value: str, realm_id: Optional[str] = None) -> Optional[str]:
        """
        Get the Vendor public_id from QBO entity reference value.

        Args:
            qbo_entity_ref_value: QBO entity reference value (QBO Vendor ID)
            realm_id: Optional QBO realm ID for realm-scoped direct lookup

        Returns:
            str: Vendor public_id or None
        """
        if not qbo_entity_ref_value:
            return None

        # U-284v: keyed by (realm_id, ref_value), not ref_value alone — the
        # direct dbo lookup below is realm-scoped, and QBO vendor ref values
        # are only unique WITHIN a realm (small sequential integers), so a
        # ref-value-only key could serve a different realm's cached vendor
        # if this connector instance ever spanned realms.
        cache_key = (realm_id, qbo_entity_ref_value)
        if cache_key in self._vendor_cache:
            return self._vendor_cache[cache_key]

        # U-313: dbo.Vendor's native QboId/RealmId is the SOLE identity store
        # for Vendor (Wave 5 "trust dbo alone" — qbo.VendorVendor no longer
        # has a writer, see docs/design/wave5.md). No legacy hop left to fall
        # back to on a miss (removed; it had no data source left either).
        direct_vendor = self.vendor_service.read_by_qbo_identity(qbo_entity_ref_value, realm_id)
        if direct_vendor:
            verified_qbo_id = verify_identity_dbo_only(
                direct_vendor,
                read_direct_by_qbo_identity=self.vendor_service.read_by_qbo_identity,
            )
            if verified_qbo_id:
                self._vendor_cache[cache_key] = direct_vendor.public_id
                return direct_vendor.public_id

        self._vendor_cache[cache_key] = None
        return None

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

        # U-364: computed ONCE per expense (not per-line) — the dbo-only line fast
        # path's readopt step needs the full set of this pull's CURRENT QBO line
        # ids to tell a genuinely stale-identity orphan (safe to re-adopt) apart
        # from a line correctly bound elsewhere in this same expense (never steal).
        live_qbo_line_ids = frozenset(line.qbo_line_id for line in qbo_purchase_lines if line.qbo_line_id)

        for qbo_line in qbo_purchase_lines:
            try:
                line_connector.sync_from_qbo_purchase_line(
                    expense_id, expense_public_id, qbo_line, live_qbo_line_ids, realm_id
                )
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
                existing_ref = self._get_qbo_item_ref(sub_cost_code_id, realm_id)
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

            item_ref = self._get_qbo_item_ref(sub_cost_code_id, realm_id)
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

    def _get_qbo_item_ref(self, sub_cost_code_id: int, realm_id: Optional[str] = None):
        """
        Get QBO ItemRef from local sub_cost_code_id.

        U-307b: dbo-native SubCostCode.QboId direct via
        cost_code_resolver.resolve_qbo_item_ref -- no qbo.Item hop, realm-verified
        (see that module for the resolution/realm-matching contract).

        Args:
            sub_cost_code_id: Local SubCostCode database ID
            realm_id: QBO realm ID this push targets

        Returns:
            QboReferenceType with QBO item value and name, or None
        """
        from integrations.intuit.qbo.purchase.external.schemas import QboReferenceType

        item_ref = resolve_qbo_item_ref(
            sub_cost_code_id,
            realm_id,
            sub_cost_code_service=self.sub_cost_code_service,
        )
        if item_ref is None:
            logger.warning(f"No QBO Item mapping resolved for sub_cost_code_id: {sub_cost_code_id}")
            return None

        return QboReferenceType(value=item_ref.value, name=item_ref.name)

    def _get_qbo_customer_ref(self, project_id: int):
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
        from integrations.intuit.qbo.purchase.external.schemas import QboReferenceType
        from entities.project.business.service import ProjectService

        if not project_id:
            return None

        project_service = ProjectService()
        project = project_service.read_by_id(project_id)
        if not project or not project.qbo_id:
            logger.debug(f"Project {project_id} has no QBO identity (QboId) stamped")
            return None

        verified_qbo_id = verify_identity_dbo_only(
            project,
            read_direct_by_qbo_identity=project_service.read_by_qbo_identity,
        )
        if not verified_qbo_id:
            return None

        return QboReferenceType(value=verified_qbo_id, name=project.name)


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

    from entities.attachment.business.service import AttachmentService
    from entities.expense_line_item.business.service import ExpenseLineItemService
    from entities.expense_line_item_attachment.business.service import ExpenseLineItemAttachmentService

    expense_line_item_service = ExpenseLineItemService()
    expense_line_item_attachment_service = ExpenseLineItemAttachmentService()
    attachment_service = AttachmentService()

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
        # U-300b (pull-side repoint) made the local dbo.Attachment.QboId identity
        # the sole source of truth for every attachable this loop ever sees — the
        # qbo.AttachableAttachment mapping-table fallback U-279 added here is
        # confirmed dead (U-315) and was removed; see TODO.md "U-300b follow-ups".
        attachment = None
        if qbo_attachable.qbo_id:
            attachment = attachment_service.read_by_qbo_identity(qbo_attachable.qbo_id, qbo_attachable.realm_id)
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
