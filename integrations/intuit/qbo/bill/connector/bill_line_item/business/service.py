# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.bill.connector.bill_line_item.business.model import BillLineItemBillLine
from integrations.intuit.qbo.bill.connector.bill_line_item.persistence.repo import BillLineItemBillLineRepository
from integrations.intuit.qbo.bill.business.model import QboBillLine
from integrations.intuit.qbo.bill.connector.bill.persistence.repo import BillBillRepository
from integrations.intuit.qbo.bill.persistence.repo import QboBillLineRepository
from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository
from integrations.intuit.qbo.customer.connector.project.persistence.repo import CustomerProjectRepository
from entities.bill_line_item.business.service import BillLineItemService
from entities.bill_line_item.business.model import BillLineItem
from entities.bill.business.service import BillService
from entities.project.business.service import ProjectService
from entities.sub_cost_code.business.service import SubCostCodeService
from integrations.intuit.qbo.base.identity_drift import stamp_line_identity_or_warn
from integrations.intuit.qbo.base.identity_consistency import verify_identity_dbo_only
from integrations.intuit.qbo.base.identity_fastpath import (
    raise_concurrent_write_race,
    run_line_identity_fastpath,
)
from integrations.intuit.qbo.base.cost_code_resolver import resolve_dbo_sub_cost_code
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.reconciliation_recorder import record_mapping_issue
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository

logger = logging.getLogger(__name__)


class BillLineItemConnector:
    """
    Connector service for synchronization between QboBillLine and BillLineItem modules.
    """

    def __init__(
        self,
        mapping_repo: Optional[BillLineItemBillLineRepository] = None,
        bill_line_item_service: Optional[BillLineItemService] = None,
        bill_service: Optional[BillService] = None,
        bill_bill_repo: Optional[BillBillRepository] = None,
        qbo_bill_line_repo: Optional[QboBillLineRepository] = None,
        sub_cost_code_service: Optional[SubCostCodeService] = None,
        qbo_customer_repo: Optional[QboCustomerRepository] = None,
        customer_project_repo: Optional[CustomerProjectRepository] = None,
        project_service: Optional[ProjectService] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        """Initialize the BillLineItemConnector."""
        self.mapping_repo = mapping_repo or BillLineItemBillLineRepository()
        self.bill_line_item_service = bill_line_item_service or BillLineItemService()
        self.bill_service = bill_service or BillService()
        self.bill_bill_repo = bill_bill_repo or BillBillRepository()
        self.qbo_bill_line_repo = qbo_bill_line_repo or QboBillLineRepository()
        # Cost-code resolution dep (U-307a; U-307d retired the legacy qbo.Item*
        # fallback repos) -- only passed to cost_code_resolver.resolve_dbo_sub_cost_code,
        # never used directly here.
        self.sub_cost_code_service = sub_cost_code_service
        # U-311: qbo_customer_repo/customer_project_repo are now DEAD -- the
        # legacy qbo.Customer -> qbo.CustomerProject hop that used them was
        # deleted from _resolve_project_public_id below (Wave 5 Option A).
        # Kept as accepted-but-unused constructor params rather than removed:
        # a broad set of unrelated tests construct this connector defensively
        # passing every kwarg (mirrors U-313's own deliberate deferral of the
        # identical class of dead-DI-param cleanup for Bill/Purchase's
        # vendor_vendor_repo/qbo_vendor_repo -- see TODO.md). Removal is a
        # Pass-2/simplify candidate, not this unit's job.
        self.qbo_customer_repo = qbo_customer_repo or QboCustomerRepository()
        self.customer_project_repo = customer_project_repo or CustomerProjectRepository()
        self.project_service = project_service or ProjectService()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()
        # Per-instance cache: a Bill's lines commonly share one job/customer_ref_value —
        # avoids re-resolving the identical (realm_id, qbo_customer_ref_value) pair once
        # per line. A fresh BillLineItemConnector is instantiated per Bill (see
        # BillBillConnector._sync_line_items), so this naturally scopes to one bill's
        # line-item sync, mirroring PurchaseLineExpenseLineItemConnector's _project_cache.
        self._project_cache: dict = {}

    def sync_from_qbo_bill_line(self, bill_id: int, qbo_bill_line: QboBillLine, realm_id: Optional[str] = None) -> BillLineItem:
        """
        Sync data from QboBillLine to BillLineItem module.
        
        This method:
        1. Checks if a mapping exists
        2. Creates or updates the BillLineItem accordingly
        
        Args:
            bill_id: Database ID of the Bill in our system
            qbo_bill_line: QboBillLine record
        
        Returns:
            BillLineItem: The synced BillLineItem record
        """
        # Get the Bill public_id from bill_id
        bill = self.bill_service.read_by_id(bill_id)
        if not bill:
            raise ValueError(f"Bill with id {bill_id} not found")
        
        bill_public_id = bill.public_id
        
        # Map QBO BillLine fields to BillLineItem module fields
        description = qbo_bill_line.description
        amount = qbo_bill_line.amount
        # Keep fractional quantities (e.g. 2.5 hrs) — BillLineItem.Quantity is DECIMAL.
        # Use `is not None` so a legitimate 0 quantity isn't dropped.
        qty = qbo_bill_line.qty if qbo_bill_line.qty is not None else None
        rate = qbo_bill_line.unit_price
        
        # Map markup from QBO (QBO uses percentage like 10 for 10%, convert to decimal 0.10)
        markup = None
        if qbo_bill_line.markup_percent is not None:
            markup = qbo_bill_line.markup_percent / Decimal('100')
        
        # Calculate price as UnitPrice * (1 + MarkupPercent/100)
        price = None
        if qbo_bill_line.unit_price is not None and qbo_bill_line.markup_percent is not None:
            price = qbo_bill_line.unit_price * (Decimal('1') + qbo_bill_line.markup_percent / Decimal('100'))
        
        # Determine billable and billed status from QBO BillableStatus
        # QBO BillableStatus values: "Billable" (not yet invoiced), "HasBeenBilled" (already invoiced), "NotBillable"
        # - is_billable: True if marked billable (regardless of whether already billed)
        # - is_billed: True if the expense has already been invoiced to a customer
        is_billable = None
        is_billed = None
        if qbo_bill_line.billable_status:
            is_billable = qbo_bill_line.billable_status in ("Billable", "HasBeenBilled")
            is_billed = qbo_bill_line.billable_status == "HasBeenBilled"
        
        # Look up SubCostCode from QBO Item reference (U-307a: dbo-native
        # dbo-native SubCostCode.QboId (U-307d retired the legacy qbo.Item ->
        # qbo.ItemSubCostCode fallback — see cost_code_resolver.py).
        sub_cost_code = resolve_dbo_sub_cost_code(
            qbo_bill_line.item_ref_value,
            realm_id,
            sub_cost_code_service=self.sub_cost_code_service,
        )
        sub_cost_code_id = sub_cost_code.id if sub_cost_code else None
        if qbo_bill_line.item_ref_value:
            if sub_cost_code_id:
                logger.debug(f"Found SubCostCode {sub_cost_code_id} for QboItem ref {qbo_bill_line.item_ref_value}")
            else:
                logger.debug(f"No SubCostCode resolved for QboItem ref {qbo_bill_line.item_ref_value}")
        
        # Look up Project from QBO Customer reference (customer_ref can be a job/sub-customer which maps to Project)
        project_public_id = None
        if qbo_bill_line.customer_ref_value:
            project_public_id = self._get_project_public_id(qbo_bill_line.customer_ref_value, realm_id)
        
        def _apply_line_fields(direct: BillLineItem, *, path_label: str) -> Optional[BillLineItem]:
            """
            Write the QBO-derived fields onto an existing, matched BillLineItem.
            Shared by two call sites (unlike the header's _apply_bill_fields,
            which is shared by more) — the field list above is already computed
            once and reused by every branch; this closure just applies it via
            update_by_public_id. `path_label` names which call site is calling
            (fast path vs legacy "mapping found"), threaded into every log
            line below so on-call log triage never misattributes a legacy-path
            failure to the fast path or vice versa.

            Identity IS still re-stamped here on every touch, mirroring the
            legacy path's own prior stamp_line_identity_or_warn call exactly
            (U-293 Gate-2 live-data equivalence check found why it must be: a
            fast-path hit means dbo.QboId already matches, but RealmId is NOT
            guaranteed to — live prod carries rows with QboId stamped correctly
            and RealmId still NULL, e.g. from a partial historical stamp. The
            legacy path self-heals those on every touch because its own stamp
            call was unconditional; a fast path that skipped this would find
            such a row forever afterward and never correct it again — a silent
            regression an initial "nothing would ever change" assumption
            missed).

            U-293-dw: "unconditional" above is no longer literally true — the
            stamp call passes enforce_realm_pairing=True, so it can skip the
            write outright when neither this call's realm_id nor the row's
            own already-stamped realm_id (the `realm_id or getattr(direct,
            "realm_id", None)` fallback right below) resolves. That fallback
            is exactly what keeps the self-heal-on-every-touch guarantee
            described above intact for an already realm-complete row (the
            overwhelming majority of touches) — it only actually skips for a
            row that has never had a real realm anywhere, which is precisely
            the state that must NOT be silently half-stamped.
            """
            updated = self.bill_line_item_service.update_by_public_id(
                direct.public_id,
                bill_public_id=bill_public_id,
                sub_cost_code_id=sub_cost_code_id,
                project_public_id=project_public_id,
                description=description,
                quantity=qty,
                rate=rate,
                amount=amount,
                is_billable=is_billable,
                is_billed=is_billed,
                markup=markup,
                price=price,
                is_draft=False,
                row_version=direct.row_version,
            )
            if updated is None:
                # ROWVERSION race: a concurrent writer touched this exact
                # BillLineItem between the read and this UPDATE.
                logger.error(
                    f"Failed to update BillLineItem {direct.id} from QboBillLine "
                    f"{qbo_bill_line.id} - update_by_public_id returned None "
                    f"(concurrent write race, {path_label})"
                )
                raise_concurrent_write_race(
                    entity_label="BillLineItem", entity_id=direct.id, path_label=path_label
                )
            stamp_line_identity_or_warn(
                self.bill_line_item_service.repo,
                id=int(updated.id),
                qbo_id=qbo_bill_line.qbo_line_id,
                # U-293-dw: fall back to the row's own already-stamped realm_id
                # when this call's realm_id is empty — an UPDATE touch on a
                # line that's already realm-complete must still re-stamp QboId
                # (e.g. QBO recycled the line id), not get skipped by the new
                # atomic-pair guard in stamp_line_identity_or_warn just because
                # this particular caller didn't have a fresh realm_id in hand.
                realm_id=realm_id or getattr(direct, "realm_id", None),
                context=f"Updated BillLineItem {updated.id} ({path_label})",
                enforce_realm_pairing=True,
            )
            return updated

        # Memoized: qbo_bill_line.id is fixed for this whole call, and both the
        # fast path (via resolve_mapping_state, on a MISSING/CONFLICT
        # classification) and the legacy path just below it (unconditionally,
        # on a fast-path miss) ask this exact same question — a MISSING
        # classification proves resolve_mapping_state already got `None` back
        # for it, so re-querying at the legacy path's own read_by_qbo_bill_line_id
        # call would be a guaranteed-duplicate round trip on every "QBO
        # recycled this line id" case, which the MISSING-never-self-heals fix
        # above makes the ordinary path, not a rare one. Still lazy (only
        # queried if resolve_mapping_state actually needs it), just not
        # re-queried once already known.
        _qbo_bill_line_mapping_cache = {}

        def _read_by_qbo_bill_line_id_cached(qbo_bill_line_id):
            if qbo_bill_line_id not in _qbo_bill_line_mapping_cache:
                _qbo_bill_line_mapping_cache[qbo_bill_line_id] = (
                    self.mapping_repo.read_by_qbo_bill_line_id(qbo_bill_line_id)
                )
            return _qbo_bill_line_mapping_cache[qbo_bill_line_id]

        # U-293 (Phase-4, lines): resolve identity directly against
        # dbo.BillLineItem's native QboId, scoped to this line's own parent Bill
        # (U-238b), before falling back to the qbo.BillLineItemBillLine
        # mapping-table hop below. A QBO line id is unique only WITHIN its
        # parent transaction (confirmed against live prod: real duplicate
        # QboId values ARE reused across different parents) — never a bare
        # global QboId lookup, unlike the header fast path. conflict->RAISE is
        # structural (base.identity_fastpath.run_line_identity_fastpath), never
        # a fall-through to the legacy path below (U-287's closing lesson).
        outcome = run_line_identity_fastpath(
            parent_local_id=bill_id,
            qbo_line_id=qbo_bill_line.qbo_line_id,
            external_id=qbo_bill_line.id,
            entity_label="BillLineItem",
            external_label="QboBillLine",
            read_direct_by_parent_and_qbo_line_id=self.bill_line_item_service.read_by_qbo_identity,
            read_by_local_id=self.mapping_repo.read_by_bill_line_item_id,
            read_by_external_id=_read_by_qbo_bill_line_id_cached,
            external_id_attr="qbo_bill_line_id",
            record_conflict_issue=lambda entity, by_local, by_external: (
                self._raise_line_identity_mapping_conflict_issue(
                    qbo_bill_line=qbo_bill_line,
                    dbo_line_id=coerce_id(entity.id),
                    local_side_mapping=by_local,
                    qbo_side_mapping=by_external,
                    realm_id=realm_id,
                )
            ),
            conflict_message=lambda entity: (
                f"BillLineItemBillLine identity conflict for QboBillLine "
                f"{qbo_bill_line.qbo_line_id} (id={qbo_bill_line.id}) on Bill "
                f"{bill_id}: dbo.BillLineItem {entity.id} already carries this "
                f"identity but the mapping table disagrees. Not auto-repointed; "
                f"see the recorded reconciliation issue. Skipping until a human "
                f"resolves it."
            ),
            apply_fields=lambda direct: _apply_line_fields(direct, path_label="line fast path"),
        )
        if outcome.hit:
            return outcome.entity

        # Check for existing mapping by current qbo_bill_line.id. Memoized
        # above — if the fast path already asked this (MISSING/CONFLICT), this
        # is a cache hit, not a second round trip.
        mapping = _read_by_qbo_bill_line_id_cached(qbo_bill_line.id)

        if not mapping:
            # Shape B fallback (task #17): when no direct mapping exists, look for
            # an orphaned BillLineItem on this bill whose content matches this QBO
            # line. This handles the case where QBO regenerates line IDs after a
            # bill edit — the old QboBillLine row is deleted by stale-line cleanup,
            # leaving the local BillLineItem unmapped. Matching by content
            # (description, amount, qty, rate) re-adopts the correct local line
            # rather than creating a duplicate.
            unmapped = self._find_unmapped_line_items(bill_id)
            orphan = self._match_by_fingerprint(
                unmapped=unmapped,
                description=description,
                amount=amount,
                qty=qty,
                rate=rate,
            )
            if orphan is not None:
                logger.info(
                    f"Adopting orphaned BillLineItem {orphan.id} for QboBillLine {qbo_bill_line.id} "
                    f"via content fingerprint match (QBO line ID regenerated or re-ordered)"
                )
                try:
                    mapping = self.mapping_repo.create(
                        bill_line_item_id=orphan.id,
                        qbo_bill_line_id=qbo_bill_line.id,
                    )
                except ValueError as error:
                    logger.warning(
                        f"Could not adopt orphaned BillLineItem {orphan.id}: {error}"
                    )

        if mapping:
            # Found existing mapping - update the BillLineItem
            line_item = self.bill_line_item_service.read_by_id(mapping.bill_line_item_id)
            if line_item:
                logger.info(f"Updating existing BillLineItem {line_item.id} from QboBillLine {qbo_bill_line.id}")
                # U-293: reuse the SAME _apply_line_fields closure the fast path
                # uses (update + identity re-stamp), mirroring BillBillConnector's
                # header-level _apply_bill_fields reuse — one update-logic site,
                # not two hand-copies that could drift.
                return _apply_line_fields(line_item, path_label="legacy mapping-table path")
            else:
                # Mapping exists but BillLineItem not found - recreate
                logger.warning(f"Mapping exists but BillLineItem {mapping.bill_line_item_id} not found. Creating new.")
                self.mapping_repo.delete_by_id(mapping.id)
                mapping = None
        
        # Create new BillLineItem
        logger.info(f"Creating new BillLineItem from QboBillLine {qbo_bill_line.id}")
        line_item = self.bill_line_item_service.create(
            bill_public_id=bill_public_id,
            sub_cost_code_id=sub_cost_code_id,
            project_public_id=project_public_id,
            description=description,
            quantity=qty,
            rate=rate,
            amount=amount,
            is_billable=is_billable,
            is_billed=is_billed,
            markup=markup,
            price=price,
            is_draft=False
        )
        
        # Create mapping
        line_item_id = coerce_id(line_item.id)
        # Deliberately ValueError-only (not DatabaseConstraintError): dbo.BillLineItem carries no
        # uniqueness constraint of any kind (unlike dbo.Bill, protected by
        # UQ_Bill_VendorId_BillNumber_BillDate), so a concurrent-pull race that loses this mapping
        # insert must NOT be silently swallowed here — the just-created `line_item` above would be
        # an undetectable, permanently duplicate row with no reconciliation trail. Left uncaught, a
        # DatabaseConstraintError propagates to _sync_line_items' per-line catch, which raises
        # RuntimeError and correctly triggers rollback_orphan_header (new-bill path) or holds the
        # watermark (existing-bill path) — the pre-existing, self-healing behavior. See U-228
        # Pass-1 hunt (confirmed P1) and the TODO.md follow-up for a proper adopt-on-race heal.
        try:
            mapping = self.create_mapping(bill_line_item_id=line_item_id, qbo_bill_line_id=qbo_bill_line.id)
            logger.info(f"Created mapping: BillLineItem {line_item_id} <-> QboBillLine {qbo_bill_line.id}")
        except ValueError as e:
            logger.warning(f"Could not create mapping: {e}")

        # U-238b: dbo line identity dual-write (create+update pairing for U-238c).
        # Mapping is already committed — a stamp failure must NOT roll back the line item.
        stamp_line_identity_or_warn(
            self.bill_line_item_service.repo,
            id=line_item_id,
            qbo_id=qbo_bill_line.qbo_line_id,
            realm_id=realm_id,
            context=f"Created mapping for BillLineItem {line_item_id} for QboBillLine {qbo_bill_line.id}",
            enforce_realm_pairing=True,
        )

        return line_item

    def _raise_line_identity_mapping_conflict_issue(
        self,
        *,
        qbo_bill_line: QboBillLine,
        dbo_line_id: int,
        local_side_mapping,
        qbo_side_mapping,
        realm_id: Optional[str] = None,
    ) -> None:
        """
        Record a dbo-identity <-> mapping-table split found by
        run_line_identity_fastpath's resolve_mapping_state. Mirrors
        BillBillConnector._raise_identity_mapping_conflict_issue exactly,
        scoped to the line level — covers all three conflict shapes (qbo-side
        only, local-side only, or both) in ONE issue, never silently dropping
        either side's blocker.
        """
        parts = [
            f"BillLineItemBillLine identity conflict. dbo.BillLineItem {dbo_line_id} "
            f"carries native QBO identity for QboBillLine {qbo_bill_line.id} "
            f"(QboLineId={qbo_bill_line.qbo_line_id})."
        ]
        if qbo_side_mapping:
            parts.append(
                f"qbo-side: the mapping table still binds that same QboBillLine to a "
                f"DIFFERENT BillLineItem {qbo_side_mapping.bill_line_item_id} "
                f"(mapping {qbo_side_mapping.id})."
            )
        if local_side_mapping:
            parts.append(
                f"local-side: BillLineItem {dbo_line_id}'s own mapping row (mapping "
                f"{local_side_mapping.id}) still binds it to a DIFFERENT QboBillLine "
                f"{local_side_mapping.qbo_bill_line_id}."
            )
        parts.append("Not auto-repointed — investigate which side is correct.")
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="bill_line_item_identity_conflict",
            entity_type="BillLineItem",
            entity_public_id=None,
            qbo_id=str(qbo_bill_line.qbo_line_id) if qbo_bill_line.qbo_line_id else None,
            realm_id=realm_id or "",
            details=" ".join(parts),
        )

    # One of FOUR near-identical QBO customer-ref -> Project resolvers (invoice /
    # purchase / vendorcredit / bill). All four are realm-scoped as of U-060; they
    # still diverge on heal (invoice only) and caching (invoice + purchase only).
    # Lift into one shared resolver when multi-realm lands — see TODO.md.
    def _get_project_public_id(self, qbo_customer_ref_value: str, realm_id: Optional[str] = None) -> Optional[str]:
        """
        Get the Project public_id from QBO customer reference value, cached per
        (realm_id, qbo_customer_ref_value) for this connector instance's lifetime
        — a Bill's lines commonly share one job/customer_ref_value, and a fresh
        connector is instantiated per Bill (BillBillConnector._sync_line_items).
        """
        if not qbo_customer_ref_value:
            return None

        cache_key = (realm_id, qbo_customer_ref_value)
        if cache_key in self._project_cache:
            return self._project_cache[cache_key]

        result = self._resolve_project_public_id(qbo_customer_ref_value, realm_id)
        self._project_cache[cache_key] = result
        return result

    def _resolve_project_public_id(self, qbo_customer_ref_value: str, realm_id: Optional[str] = None) -> Optional[str]:
        """
        Uncached resolution — see _get_project_public_id, which caches this.

        Args:
            qbo_customer_ref_value: QBO customer reference value (QBO Customer ID)
            realm_id: Optional QBO realm ID for realm-scoped customer lookup

        Returns:
            str: Project public_id or None
        """
        # U-283 §10 prereq: try dbo.Project's native QboId/RealmId directly
        # first (mirrors U-276's push-side verify pattern). Every Project
        # synced at least once already carries this identity via
        # SetProjectQboIdentity. Read-only resolver — a miss/refusal returns
        # None outright (no write here to protect, unlike the header identity
        # fast path).
        #
        # U-311 (Wave-5 Option A): the verify step is now
        # `verify_identity_dbo_only` — a plain re-read of dbo.Project by the
        # resolved row's OWN (qbo_id, realm_id), trusted only when it still
        # resolves back to the same local id — and reads NO `qbo.*` mapping
        # table at all. There is no legacy hop left: the old
        # `qbo.Customer` -> `qbo.CustomerProject` hop was this resolver's only
        # other data source, and Wave 5 retires that mapping table. Per
        # `docs/design/wave5.md` §2's "consequence worth flagging": this
        # resolver used to be ADVISORY (a verify disagreement degraded
        # gracefully to the slower legacy hop); once the mapping table's data
        # source is gone it becomes hard-stop-equivalent BY CONSTRUCTION, not
        # by choice. Measured as a no-op today (0 dbo<->mapping disagreements
        # live), but a future disagreement that used to degrade now resolves
        # to None — the line simply syncs without a Project binding rather
        # than binding to an unverified parent, the safe side of that trade.
        direct_project = self.project_service.read_by_qbo_identity(qbo_customer_ref_value, realm_id)
        if direct_project:
            verified_qbo_id = verify_identity_dbo_only(
                direct_project,
                read_direct_by_qbo_identity=self.project_service.read_by_qbo_identity,
            )
            if verified_qbo_id:
                logger.debug(f"Found Project {direct_project.id} via direct dbo QboId lookup")
                return direct_project.public_id
        return None

    # ------------------------------------------------------------------ #
    # Shape B line-matching helpers (task #17)
    # ------------------------------------------------------------------ #

    def _find_unmapped_line_items(self, bill_id: int):
        """Return BillLineItems on this bill that have no QboBillLine mapping."""
        existing = self.bill_line_item_service.read_by_bill_id(bill_id)
        return [
            li for li in existing
            if not self.mapping_repo.read_by_bill_line_item_id(li.id)
        ]

    @staticmethod
    def _normalize_for_fingerprint(value) -> str:
        """Canonicalize a value for content-fingerprint comparison."""
        if value is None:
            return ""
        # Decimals and floats: normalize to a fixed-precision string so 10 == 10.00.
        if isinstance(value, Decimal):
            return format(value.normalize(), "f")
        try:
            return format(Decimal(str(value)).normalize(), "f")
        except Exception:
            pass
        return str(value).strip()

    def _match_by_fingerprint(
        self,
        *,
        unmapped,
        description,
        amount,
        qty,
        rate,
    ):
        """
        Find an unmapped line item whose content fingerprint matches, POSITION-AWARE.

        The fingerprint is `(description, amount, quantity, rate)`. When several
        unmapped lines share a fingerprint (a 50-50 split, repeated draws), we return
        the FIRST in stable position order (by id ≈ creation ≈ LineNum). The caller
        consumes it (creates a mapping) before the next QBO line, so processing QBO
        lines in order pairs identical-content lines 1:1 by position. This is robust
        to QBO regenerating line ids even with duplicate content, instead of bailing
        and creating duplicates. Returns None only when nothing matches.
        """
        target = (
            self._normalize_for_fingerprint(description),
            self._normalize_for_fingerprint(amount),
            self._normalize_for_fingerprint(qty),
            self._normalize_for_fingerprint(rate),
        )

        matches = []
        for candidate in sorted(unmapped, key=lambda c: getattr(c, "id", 0) or 0):
            candidate_fp = (
                self._normalize_for_fingerprint(getattr(candidate, "description", None)),
                self._normalize_for_fingerprint(getattr(candidate, "amount", None)),
                self._normalize_for_fingerprint(getattr(candidate, "quantity", None)),
                self._normalize_for_fingerprint(getattr(candidate, "rate", None)),
            )
            if candidate_fp == target:
                matches.append(candidate)

        if matches:
            if len(matches) > 1:
                logger.info(
                    f"{len(matches)} unmapped BillLineItems share (description, amount, qty, rate); "
                    f"adopting the first by position (QBO line-id regeneration)"
                )
            return matches[0]
        return None

    def create_mapping(self, bill_line_item_id: int, qbo_bill_line_id: int) -> BillLineItemBillLine:
        """
        Create a mapping between BillLineItem and QboBillLine.
        
        Args:
            bill_line_item_id: Database ID of BillLineItem record
            qbo_bill_line_id: Database ID of QboBillLine record
        
        Returns:
            BillLineItemBillLine: The created mapping record
        
        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints
        existing_by_line_item = self.mapping_repo.read_by_bill_line_item_id(bill_line_item_id)
        if existing_by_line_item:
            raise ValueError(
                f"BillLineItem {bill_line_item_id} is already mapped to QboBillLine {existing_by_line_item.qbo_bill_line_id}"
            )
        
        existing_by_qbo_line = self.mapping_repo.read_by_qbo_bill_line_id(qbo_bill_line_id)
        if existing_by_qbo_line:
            raise ValueError(
                f"QboBillLine {qbo_bill_line_id} is already mapped to BillLineItem {existing_by_qbo_line.bill_line_item_id}"
            )
        
        # Create mapping
        return self.mapping_repo.create(bill_line_item_id=bill_line_item_id, qbo_bill_line_id=qbo_bill_line_id)

    def get_mapping_by_bill_line_item_id(self, bill_line_item_id: int) -> Optional[BillLineItemBillLine]:
        """
        Get mapping by BillLineItem ID.
        """
        return self.mapping_repo.read_by_bill_line_item_id(bill_line_item_id)

    def get_mapping_by_qbo_bill_line_id(self, qbo_bill_line_id: int) -> Optional[BillLineItemBillLine]:
        """
        Get mapping by QboBillLine ID.
        """
        return self.mapping_repo.read_by_qbo_bill_line_id(qbo_bill_line_id)
