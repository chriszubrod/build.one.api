# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.vendorcredit.business.model import QboVendorCreditLine
from integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.persistence.repo import VendorCreditLineItemBillCreditLineItemMappingRepository
from entities.bill_credit_line_item.business.service import BillCreditLineItemService
from entities.bill_credit_line_item.business.model import BillCreditLineItem
from entities.project.business.service import ProjectService
from entities.sub_cost_code.business.service import SubCostCodeService
from integrations.intuit.qbo.base.identity_drift import stamp_line_identity_or_warn
from integrations.intuit.qbo.base.identity_fastpath import (
    raise_concurrent_write_race,
    run_line_identity_fastpath,
)
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.reconciliation_recorder import record_mapping_issue
from integrations.intuit.qbo.base.cost_code_resolver import resolve_dbo_sub_cost_code
from integrations.intuit.qbo.item.connector.sub_cost_code.persistence.repo import ItemSubCostCodeRepository
from integrations.intuit.qbo.item.persistence.repo import QboItemRepository
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository

logger = logging.getLogger(__name__)


class VendorCreditLineItemConnector:
    """Connector for syncing QBO VendorCredit lines to BillCreditLineItems."""

    def __init__(self, reconciliation_repo: Optional[ReconciliationIssueRepository] = None):
        self.bill_credit_line_item_service = BillCreditLineItemService()
        self.project_service = ProjectService()
        self.sub_cost_code_service = SubCostCodeService()
        self.mapping_repo = VendorCreditLineItemBillCreditLineItemMappingRepository()
        self.qbo_item_repo = QboItemRepository()
        self.item_scc_repo = ItemSubCostCodeRepository()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()
        # Run-scoped memo (U-229) — same shape as the sibling PurchaseLineExpenseLineItemConnector's
        # _sub_cost_code_cache (purchase/connector/expense_line_item/business/service.py), which
        # already ships this exact pattern in prod: keyed by qbo_item_ref_value, caches hits AND
        # misses, never invalidated for the life of this instance. Accepted tradeoff, not a bug: a
        # mid-run repoint or delete of the underlying ItemSubCostCode/SubCostCode (a rare,
        # permission-gated admin action) can feed a stale id to a later line in the same run. That
        # fails safe — a stale id pointing at a deleted SubCostCode trips the BillCreditLineItem FK,
        # which the per-line try/except in _sync_line_items turns into a per-credit skip that
        # retries next run, never partial/corrupt data; a stale id pointing at a repointed-but-
        # still-valid SubCostCode at worst misattributes one line's cost coding until the next pull.
        self._sub_cost_code_cache: dict = {}
        # Run-scoped memo (U-278) — same shape/tradeoffs as _sub_cost_code_cache above,
        # keyed by (qbo_customer_ref_value, realm_id). A mid-run repoint of the
        # underlying Project (rare, permission-gated) can feed a stale public_id to a
        # later line in the same run; that fails safe the same way (an FK/access check
        # downstream turns it into a per-credit skip that retries next run).
        self._project_public_id_cache: dict = {}

    def sync_from_qbo_line(
        self,
        bill_credit_id: int,
        bill_credit_public_id: str,
        qbo_line: QboVendorCreditLine,
        realm_id: Optional[str] = None,
    ) -> BillCreditLineItem:
        """
        Upsert a QBO VendorCredit line into a BillCreditLineItem.

        Matches an existing BillCreditLineItem via the (now-stable)
        VendorCreditLineItemBillCreditLineItem mapping keyed on qbo_line.id; if the
        mapping is missing (e.g. QBO regenerated the line id on an edit) it falls
        back to a content fingerprint to adopt the orphaned local line instead of
        duplicating it. Updates in place when matched, creates otherwise.
        Prefers ItemBasedExpenseLineDetail when available.

        Raises on any projection failure; the parent fails the whole credit so the
        watermark holds and it retries.
        """
        # Resolve project from CustomerRef (if billable)
        project_public_id = None
        if qbo_line.customer_ref_value:
            project_public_id = self._get_project_public_id(qbo_line.customer_ref_value, realm_id)

        # Resolve sub_cost_code from ItemRef
        sub_cost_code_id = None
        if qbo_line.item_ref_value:
            sub_cost_code_id = self._get_sub_cost_code_id(qbo_line.item_ref_value, realm_id)

        # Determine billable and billed status from QBO BillableStatus.
        # Leave both None when QBO omits the status so the in-place UPDATE
        # PRESERVES the local value instead of regressing an already-billed line
        # back to not-billed on a re-pull (mirrors the Bill connector).
        # "Billable" = not yet invoiced, "HasBeenBilled" = already invoiced, "NotBillable" = not billable
        is_billable = None
        is_billed = None
        if qbo_line.billable_status:
            is_billable = qbo_line.billable_status in ("Billable", "HasBeenBilled")
            is_billed = qbo_line.billable_status == "HasBeenBilled"

        # Calculate billable amount (same as amount if billable)
        billable_amount = qbo_line.amount if is_billable else None

        def _apply_line_fields(direct: BillCreditLineItem, *, path_label: str) -> Optional[BillCreditLineItem]:
            """
            Write the QBO-derived fields onto an existing, matched
            BillCreditLineItem. Shared by the fast path and the legacy
            "existing" branch (U-293b, mirroring BillLineItemConnector's
            _apply_line_fields) — one update-logic site, not two hand-copies
            that could drift.
            """
            updated = self.bill_credit_line_item_service.update_by_public_id(
                direct.public_id,
                row_version=direct.row_version,
                sub_cost_code_id=sub_cost_code_id,
                project_public_id=project_public_id,
                description=qbo_line.description,
                quantity=qbo_line.qty,
                unit_price=qbo_line.unit_price,
                amount=qbo_line.amount,
                is_billable=is_billable,
                is_billed=is_billed,
                billable_amount=billable_amount,
                is_draft=False,
            )
            if updated is None:
                # ROWVERSION race: a concurrent writer touched this exact
                # BillCreditLineItem between the read and this UPDATE.
                logger.error(
                    f"Failed to update BillCreditLineItem {direct.id} from "
                    f"QboVendorCreditLine {qbo_line.id} - update_by_public_id "
                    f"returned None (concurrent write race, {path_label})"
                )
                raise_concurrent_write_race(
                    entity_label="BillCreditLineItem", entity_id=direct.id, path_label=path_label
                )
            # U-238b: dbo line identity dual-write (create+update pairing for U-238c).
            stamp_line_identity_or_warn(
                self.bill_credit_line_item_service.repo,
                id=int(updated.id),
                qbo_id=qbo_line.qbo_line_id,
                # U-293-dw fold-in: fall back to the row's own already-stamped
                # realm_id when this call's realm_id is empty (see
                # BillLineItemConnector's identical fallback for the full
                # rationale).
                realm_id=realm_id or getattr(direct, "realm_id", None),
                context=f"Updated BillCreditLineItem {updated.id} ({path_label})",
                enforce_realm_pairing=True,
            )
            return updated

        # Memoized: qbo_line.id is fixed for this whole call, and both the fast
        # path (via resolve_mapping_state, on a MISSING/CONFLICT classification)
        # and the legacy path just below it (unconditionally, on a fast-path
        # miss) ask this exact same question — mirrors BillLineItemConnector's
        # identical memoization.
        _qbo_line_mapping_cache = {}

        def _read_by_qbo_line_id_cached(qbo_vendor_credit_line_id):
            if qbo_vendor_credit_line_id not in _qbo_line_mapping_cache:
                _qbo_line_mapping_cache[qbo_vendor_credit_line_id] = (
                    self.mapping_repo.read_by_qbo_line_id(qbo_vendor_credit_line_id)
                )
            return _qbo_line_mapping_cache[qbo_vendor_credit_line_id]

        # U-293b: resolve identity directly against dbo.BillCreditLineItem's
        # native QboId, scoped to this line's own parent BillCredit (U-238b),
        # before falling back to the
        # qbo.VendorCreditLineItemBillCreditLineItem mapping-table hop below.
        # Mirrors BillLineItemConnector's U-293 pilot exactly. conflict->RAISE
        # is structural, never a fall-through to the legacy path.
        if qbo_line.id:
            outcome = run_line_identity_fastpath(
                parent_local_id=bill_credit_id,
                qbo_line_id=qbo_line.qbo_line_id,
                external_id=qbo_line.id,
                entity_label="BillCreditLineItem",
                external_label="QboVendorCreditLine",
                read_direct_by_parent_and_qbo_line_id=self.bill_credit_line_item_service.read_by_qbo_identity,
                read_by_local_id=self.mapping_repo.read_by_bill_credit_line_item_id,
                read_by_external_id=_read_by_qbo_line_id_cached,
                external_id_attr="qbo_vendor_credit_line_id",
                record_conflict_issue=lambda entity, by_local, by_external: (
                    self._raise_line_identity_mapping_conflict_issue(
                        qbo_line=qbo_line,
                        dbo_line_id=coerce_id(entity.id),
                        local_side_mapping=by_local,
                        qbo_side_mapping=by_external,
                        realm_id=realm_id,
                    )
                ),
                conflict_message=lambda entity: (
                    f"VendorCreditLineItemBillCreditLineItem identity conflict for "
                    f"QboVendorCreditLine {qbo_line.qbo_line_id} (id={qbo_line.id}) on "
                    f"BillCredit {bill_credit_id}: dbo.BillCreditLineItem {entity.id} "
                    f"already carries this identity but the mapping table disagrees. "
                    f"Not auto-repointed; see the recorded reconciliation issue. "
                    f"Skipping until a human resolves it."
                ),
                apply_fields=lambda direct: _apply_line_fields(direct, path_label="line fast path"),
            )
            if outcome.hit:
                return outcome.entity

        # --- Find an existing BillCreditLineItem to update in place ---
        existing = None
        mapping = _read_by_qbo_line_id_cached(qbo_line.id) if qbo_line.id else None
        if mapping and mapping.bill_credit_line_item_id:
            existing = self.bill_credit_line_item_service.read_by_id(mapping.bill_credit_line_item_id)
            if not existing:
                # Dangling mapping (line deleted out from under it) — drop it.
                try:
                    self.mapping_repo.delete_by_id(mapping.id)
                except Exception as e:
                    logger.warning(f"Could not delete dangling line mapping {mapping.id}: {e}")
                mapping = None

        if existing is None and bill_credit_id and qbo_line.id:
            # Fingerprint fallback: adopt an unmapped local line with matching
            # content (QBO regenerated the line id) rather than duplicating.
            orphan = self._match_unmapped_by_fingerprint(bill_credit_id, qbo_line)
            if orphan is not None:
                try:
                    self.mapping_repo.create(
                        qbo_vendor_credit_line_id=qbo_line.id,
                        bill_credit_line_item_id=orphan.id,
                    )
                    existing = orphan
                    logger.info(
                        f"Adopted orphaned BillCreditLineItem {orphan.id} for "
                        f"QboVendorCreditLine {qbo_line.id} via content fingerprint"
                    )
                except Exception as e:
                    logger.warning(f"Could not adopt orphaned BillCreditLineItem {orphan.id}: {e}")

        if existing is not None:
            # U-293b: reuse the SAME _apply_line_fields closure the fast path
            # uses (update + identity re-stamp).
            return _apply_line_fields(existing, path_label="legacy mapping-table path")

        # --- No match: create a new line item + mapping ---
        line_item = self.bill_credit_line_item_service.create(
            bill_credit_public_id=bill_credit_public_id,
            sub_cost_code_id=sub_cost_code_id,
            project_public_id=project_public_id,
            description=qbo_line.description,
            quantity=qbo_line.qty,
            unit_price=qbo_line.unit_price,
            amount=qbo_line.amount,
            is_billable=is_billable,
            is_billed=is_billed,
            billable_amount=billable_amount,
            is_draft=False,
        )

        # Create VendorCreditLine <-> BillCreditLineItem mapping so that
        # LinkedTxn references can be resolved when syncing invoices to QBO.
        # qbo_line.id is the stable local PK of the QboVendorCreditLine record
        # (the snapshot layer now upserts lines in place).
        if line_item and qbo_line.id:
            try:
                self.mapping_repo.create(
                    qbo_vendor_credit_line_id=qbo_line.id,
                    bill_credit_line_item_id=line_item.id,
                )
            except Exception as mapping_err:
                # Deliberately swallow mapping failure to warning (bill-style): the line
                # IS persisted so the header total still balances (the invariant the
                # parent's RuntimeError protects), and _match_unmapped_by_fingerprint
                # re-adopts the unmapped line on the next pull. We do NOT follow the
                # purchase sibling's rollback-and-raise because that would delete a good
                # line over a mapping blip.
                logger.warning(
                    f"Created BillCreditLineItem {line_item.id} but could not create "
                    f"VendorCreditLineItemBillCreditLineItem mapping: {mapping_err}"
                )
            else:
                # U-238b: dbo line identity dual-write (create+update pairing for U-238c).
                stamp_line_identity_or_warn(
                    self.bill_credit_line_item_service.repo,
                    id=int(line_item.id),
                    qbo_id=qbo_line.qbo_line_id,
                    realm_id=realm_id,
                    context=(
                        f"Created BillCreditLineItem {line_item.id} mapping for "
                        f"QboVendorCreditLine {qbo_line.id}"
                    ),
                    enforce_realm_pairing=True,
                )

        return line_item

    def _raise_line_identity_mapping_conflict_issue(
        self,
        *,
        qbo_line: QboVendorCreditLine,
        dbo_line_id: int,
        local_side_mapping,
        qbo_side_mapping,
        realm_id: Optional[str] = None,
    ) -> None:
        """
        Record a dbo-identity <-> mapping-table split found by
        run_line_identity_fastpath's resolve_mapping_state. Mirrors
        BillLineItemConnector._raise_line_identity_mapping_conflict_issue
        exactly, scoped to the bill-credit line level — covers all three
        conflict shapes (qbo-side only, local-side only, or both) in ONE
        issue, never silently dropping either side's blocker.
        """
        parts = [
            f"VendorCreditLineItemBillCreditLineItem identity conflict. "
            f"dbo.BillCreditLineItem {dbo_line_id} carries native QBO identity "
            f"for QboVendorCreditLine {qbo_line.id} (QboLineId={qbo_line.qbo_line_id})."
        ]
        if qbo_side_mapping:
            parts.append(
                f"qbo-side: the mapping table still binds that same QboVendorCreditLine to a "
                f"DIFFERENT BillCreditLineItem {qbo_side_mapping.bill_credit_line_item_id} "
                f"(mapping {qbo_side_mapping.id})."
            )
        if local_side_mapping:
            parts.append(
                f"local-side: BillCreditLineItem {dbo_line_id}'s own mapping row (mapping "
                f"{local_side_mapping.id}) still binds it to a DIFFERENT QboVendorCreditLine "
                f"{local_side_mapping.qbo_vendor_credit_line_id}."
            )
        parts.append("Not auto-repointed — investigate which side is correct.")
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="bc_line_item_identity_conflict",
            entity_type="BillCreditLineItem",
            entity_public_id=None,
            qbo_id=str(qbo_line.qbo_line_id) if qbo_line.qbo_line_id else None,
            realm_id=realm_id or "",
            details=" ".join(parts),
        )

    @staticmethod
    def _fingerprint(value) -> str:
        """Canonicalize a value for content-fingerprint comparison (10 == 10.00)."""
        if value is None:
            return ""
        if isinstance(value, Decimal):
            return format(value.normalize(), "f")
        try:
            return format(Decimal(str(value)).normalize(), "f")
        except Exception:
            return str(value).strip()

    def _match_unmapped_by_fingerprint(self, bill_credit_id: int, qbo_line: QboVendorCreditLine):
        """
        Find an unmapped BillCreditLineItem whose (description, amount, qty, unit_price)
        matches the QBO line, POSITION-AWARE. When several unmapped lines share a
        fingerprint (a 50-50 split, repeated draws), return the FIRST in stable position
        order (by id ≈ creation ≈ LineNum). The caller consumes it (creates a mapping)
        before the next QBO line, so processing lines in order pairs identical-content
        lines 1:1 by position — robust to QBO line-id regeneration even with duplicate
        content, instead of bailing and duplicating. Returns None only when nothing matches.
        """
        existing = self.bill_credit_line_item_service.read_by_bill_credit_id(bill_credit_id)
        target = (
            self._fingerprint(qbo_line.description), self._fingerprint(qbo_line.amount),
            self._fingerprint(qbo_line.qty), self._fingerprint(qbo_line.unit_price),
        )
        matches = [
            li for li in sorted(existing, key=lambda c: getattr(c, "id", 0) or 0)
            if not self.mapping_repo.read_by_bill_credit_line_item_id(li.id)
            and (
                self._fingerprint(li.description), self._fingerprint(li.amount),
                self._fingerprint(li.quantity), self._fingerprint(li.unit_price),
            ) == target
        ]
        if matches:
            if len(matches) > 1:
                logger.info(
                    f"{len(matches)} unmapped BillCreditLineItems share the fingerprint for "
                    f"QboVendorCreditLine {qbo_line.id}; adopting the first by position"
                )
            return matches[0]
        return None

    # One of FOUR near-identical QBO customer-ref -> Project resolvers (invoice /
    # purchase / vendorcredit / bill). All four are realm-scoped as of U-060; they
    # still diverge on heal (invoice only) and caching (invoice + purchase only).
    # Lift into one shared resolver when multi-realm lands — see TODO.md.
    def _get_project_public_id(self, qbo_customer_ref_value: str, realm_id: Optional[str] = None) -> Optional[str]:
        """Resolve QBO customer ref to local project public_id, memoized for this
        connector's lifetime (U-278) — same shape as `_get_sub_cost_code_id` below.
        A VendorCredit's lines commonly repeat the same CustomerRef (one project,
        several cost-coded lines); without this, each line paid its own
        ReadProjectByQboIdAndRealmId round trip for an identical key."""
        if not qbo_customer_ref_value:
            return None
        key = (qbo_customer_ref_value, realm_id)
        if key in self._project_public_id_cache:
            return self._project_public_id_cache[key]
        result = self._resolve_project_public_id(qbo_customer_ref_value, realm_id)
        self._project_public_id_cache[key] = result
        return result

    def _resolve_project_public_id(
        self, qbo_customer_ref_value: str, realm_id: Optional[str] = None
    ) -> Optional[str]:
        """Uncached resolution.

        U-278 (Phase-4 pull-resolver repoint, deferred from U-276 §10): tries the direct
        dbo-native lookup first — `ProjectService.read_by_qbo_identity` (built by U-276)
        matches `dbo.Project.QboId`/`.RealmId` against this CustomerRef value directly,
        with no `qbo.Customer`/`qbo.CustomerProject` hop. Every Project synced even once
        since U-276 already carries this identity. Falls back to the legacy
        QboCustomer-by-qbo_id -> CustomerProject-by-qbo_customer_id mapping-table hop for
        any Project that predates identity stamping — read-only, no write side (unlike
        U-276's own connector, this resolver never creates or repoints anything).
        """
        direct = self.project_service.read_by_qbo_identity(qbo_customer_ref_value, realm_id)
        if direct:
            return direct.public_id

        from integrations.intuit.qbo.customer.connector.project.persistence.repo import CustomerProjectRepository
        from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository

        qbo_customer_repo = QboCustomerRepository()
        customer_project_repo = CustomerProjectRepository()
        if realm_id:
            qbo_customer = qbo_customer_repo.read_by_qbo_id_and_realm_id(qbo_customer_ref_value, realm_id)
        else:
            qbo_customer = qbo_customer_repo.read_by_qbo_id(qbo_customer_ref_value)
        if not qbo_customer:
            return None
        mapping = customer_project_repo.read_by_qbo_customer_id(qbo_customer.id)
        if not mapping or not mapping.project_id:
            return None
        project = self.project_service.read_by_id(id=str(mapping.project_id))
        return project.public_id if project else None

    def _get_sub_cost_code_id(self, qbo_item_ref_value: str, realm_id: Optional[str] = None) -> Optional[int]:
        """Resolve QBO item ref to local sub_cost_code_id, memoized for this connector's
        lifetime. U-307a: dbo-native SubCostCode.QboId first, legacy qbo.Item ->
        qbo.ItemSubCostCode hop on a miss — see cost_code_resolver.py."""
        if not qbo_item_ref_value:
            return None
        cache_key = (realm_id, qbo_item_ref_value)
        if cache_key in self._sub_cost_code_cache:
            return self._sub_cost_code_cache[cache_key]
        result = self._resolve_sub_cost_code_id(qbo_item_ref_value, realm_id)
        self._sub_cost_code_cache[cache_key] = result
        return result

    def _resolve_sub_cost_code_id(self, qbo_item_ref_value: str, realm_id: Optional[str] = None) -> Optional[int]:
        """Uncached resolution via the shared cost-code resolver."""
        sub_cost_code = resolve_dbo_sub_cost_code(
            qbo_item_ref_value,
            realm_id,
            sub_cost_code_service=self.sub_cost_code_service,
            qbo_item_repo=self.qbo_item_repo,
            item_sub_cost_code_repo=self.item_scc_repo,
        )
        if not sub_cost_code:
            logger.warning(
                f"No SubCostCode resolved for QBO Item ref '{qbo_item_ref_value}' — "
                f"BillCreditLineItem will have no SubCostCode (billing gap)"
            )
            return None
        return sub_cost_code.id
