# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.purchase.connector.expense_line_item.business.model import PurchaseLineExpenseLineItem
from integrations.intuit.qbo.purchase.connector.expense_line_item.persistence.repo import PurchaseLineExpenseLineItemRepository
from integrations.intuit.qbo.purchase.business.model import QboPurchaseLine
from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository
from entities.expense_line_item.business.service import ExpenseLineItemService
from entities.expense_line_item.business.model import ExpenseLineItem
from entities.project.business.service import ProjectService
from entities.sub_cost_code.business.service import SubCostCodeService
from integrations.intuit.qbo.base.identity_consistency import verify_identity_dbo_only
from integrations.intuit.qbo.base.identity_drift import stamp_line_identity_or_warn
from integrations.intuit.qbo.base.identity_fastpath import (
    raise_concurrent_write_race,
    run_line_identity_fastpath,
)
from integrations.intuit.qbo.base.cost_code_resolver import resolve_dbo_sub_cost_code
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.reconciliation_recorder import record_mapping_issue
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository

logger = logging.getLogger(__name__)


# Pure pull-side field decision, deliberately NOT connector state. The identical
# AccountBasedExpenseLineDetail gap exists in the Bill and VendorCredit line
# connectors (bill/connector/bill_line_item/, vendorcredit/connector/
# bill_credit_line_item/) — when the second caller arrives, lift this into
# integrations/intuit/qbo/base/ next to preserve_human_edited_ref rather than
# pasting a copy, the way the four customer-ref resolvers went. See TODO.md.
def default_amount_only_line(qty, unit_price, amount):
    """
    Amount-only QBO line (NO Qty AND NO UnitPrice, e.g. a Ramp
    AccountBasedExpenseLineDetail) -> quantity 1 at rate=amount, so
    quantity * rate == amount. Any line that carries either field is
    returned untouched — an explicit 0 is a real value, not a missing one.
    """
    if qty is None and unit_price is None and amount is not None:
        return Decimal("1"), Decimal(str(amount))
    return qty, unit_price


def preserve_stored_value(default_value, qbo_value, stored_value):
    """
    Decide what to send for a field the pull may default.

    Returns None — the "leave it alone" sentinel — when QBO omitted the field and
    the local row already carries a value, so a re-pull never overwrites a coding-
    queue backfill. Otherwise returns the (possibly defaulted) value to write.

    NB the None sentinel is honored by ExpenseLineItemService.update_by_public_id,
    which re-reads the row and only assigns fields that arrive non-None. Sending
    None rather than echoing `stored_value` back is deliberate: the service's read
    is fresher than ours, so a concurrent web edit between our read and the write
    is preserved instead of being clobbered with a stale value.
    """
    if qbo_value is None and stored_value is not None:
        return None
    return default_value


class PurchaseLineExpenseLineItemConnector:
    """
    Connector service for synchronization between QboPurchaseLine and ExpenseLineItem.
    """

    def __init__(
        self,
        mapping_repo: Optional[PurchaseLineExpenseLineItemRepository] = None,
        expense_line_item_service: Optional[ExpenseLineItemService] = None,
        sub_cost_code_service: Optional[SubCostCodeService] = None,
        customer_project_repo=None,
        qbo_customer_repo: Optional[QboCustomerRepository] = None,
        project_service: Optional[ProjectService] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        """Initialize the PurchaseLineExpenseLineItemConnector."""
        self.mapping_repo = mapping_repo or PurchaseLineExpenseLineItemRepository()
        self.expense_line_item_service = expense_line_item_service or ExpenseLineItemService()
        # Cost-code resolution dep (U-307a; U-307d retired the legacy qbo.Item*
        # fallback repos) -- only passed to cost_code_resolver.resolve_dbo_sub_cost_code.
        self.sub_cost_code_service = sub_cost_code_service
        # Per-sync caches: the same QBO item / customer ref appears on many lines.
        # Caching avoids a repeat resolution per repeated value.
        self._sub_cost_code_cache: dict = {}  # (realm_id, qbo_item_ref_value) -> sub_cost_code_id | None
        self._project_cache: dict = {}        # (realm_id, qbo_customer_ref_value) -> project_public_id | None
        # U-311: customer_project_repo/qbo_customer_repo are now DEAD -- the
        # legacy qbo.Customer -> qbo.CustomerProject hop that used them was
        # deleted from _get_project_public_id below (Wave 5 Option A). U-314
        # dropped qbo.CustomerProject entirely, so customer_project_repo can
        # no longer default-construct its old repo class -- kept as an
        # untyped, unconstructed constructor param rather than removed
        # (mirrors U-313's own deliberate deferral of the identical class of
        # dead-DI-param cleanup). qbo_customer_repo's own class is untouched
        # by this drop and stays a live Pass-2/simplify candidate.
        self.customer_project_repo = customer_project_repo
        self.qbo_customer_repo = qbo_customer_repo or QboCustomerRepository()
        self.project_service = project_service or ProjectService()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()

    def sync_from_qbo_purchase_line(self, expense_id: int, expense_public_id: str, qbo_line: QboPurchaseLine, realm_id: Optional[str] = None) -> ExpenseLineItem:
        """
        Sync data from QboPurchaseLine to ExpenseLineItem module.

        Args:
            expense_id: Database ID of the Expense
            expense_public_id: Public ID of the Expense (passed in to avoid a per-line DB read)
            qbo_line: QboPurchaseLine record

        Returns:
            ExpenseLineItem: The synced ExpenseLineItem record
        """

        # Resolve sub_cost_code from item reference
        sub_cost_code_id = None
        if qbo_line.item_ref_value:
            sub_cost_code_id = self._get_sub_cost_code_id(qbo_line.item_ref_value, realm_id)

        # Resolve project from customer reference
        project_public_id = None
        if qbo_line.customer_ref_value:
            project_public_id = self._get_project_public_id(qbo_line.customer_ref_value, realm_id)

        # Determine billable status
        is_billable = None
        is_billed = None
        if qbo_line.billable_status:
            if qbo_line.billable_status == "Billable":
                is_billable = True
                is_billed = False
            elif qbo_line.billable_status == "HasBeenBilled":
                is_billable = True
                is_billed = True
            elif qbo_line.billable_status == "NotBillable":
                is_billable = False
                is_billed = False

        # Calculate markup (convert from percentage to decimal if needed)
        markup = None
        if qbo_line.markup_percent is not None:
            # QBO stores markup as percentage (e.g., 10 for 10%), we store as decimal (e.g., 0.10)
            markup = Decimal(str(qbo_line.markup_percent)) / Decimal('100')

        # What this pull WOULD write if the local row carried nothing (U-098). A QBO
        # amount-only line (Ramp card spend on 58999) has no Qty/UnitPrice/MarkupInfo
        # at all, which used to persist as NULL quantity/rate/markup. Derived once
        # per line; the create and update paths below decide whether to apply them.
        default_qty, default_rate = default_amount_only_line(
            qbo_line.qty, qbo_line.unit_price, qbo_line.amount
        )
        default_markup = markup if markup is not None else Decimal("0")

        # Calculate price: amount * (1 + markup), or amount if no markup.
        # Unchanged by the markup default — amount * (1 + 0) == amount.
        price = None
        if qbo_line.amount is not None:
            amount_val = Decimal(str(qbo_line.amount))
            if markup is not None:
                price = amount_val * (Decimal('1') + markup)
            else:
                price = amount_val

        def _apply_line_fields(direct: ExpenseLineItem, *, path_label: str) -> Optional[ExpenseLineItem]:
            """
            Write the QBO-derived fields onto an existing, matched ExpenseLineItem.
            Shared by the fast path and the legacy "mapping found" branch (U-293b,
            mirroring BillLineItemConnector's _apply_line_fields) — one update-logic
            site, not two hand-copies that could drift. `direct` plays the role the
            legacy path's own `line_item` used to: its CURRENT stored quantity/rate/
            markup feed preserve_stored_value exactly as before.
            """
            update_qty = preserve_stored_value(default_qty, qbo_line.qty, direct.quantity)
            update_rate = preserve_stored_value(default_rate, qbo_line.unit_price, direct.rate)
            update_markup = preserve_stored_value(default_markup, qbo_line.markup_percent, direct.markup)

            updated = self.expense_line_item_service.update_by_public_id(
                direct.public_id,
                row_version=direct.row_version,
                sub_cost_code_id=sub_cost_code_id,
                project_public_id=project_public_id,
                description=qbo_line.description,
                quantity=update_qty,
                rate=update_rate,
                amount=qbo_line.amount,
                is_billable=is_billable,
                is_billed=is_billed,
                markup=update_markup,
                price=price,
                is_draft=False,
            )
            if updated is None:
                # ROWVERSION race: a concurrent writer touched this exact
                # ExpenseLineItem between the read and this UPDATE.
                logger.error(
                    f"Failed to update ExpenseLineItem {direct.id} from QboPurchaseLine "
                    f"{qbo_line.id} - update_by_public_id returned None "
                    f"(concurrent write race, {path_label})"
                )
                raise_concurrent_write_race(
                    entity_label="ExpenseLineItem", entity_id=direct.id, path_label=path_label
                )
            stamp_line_identity_or_warn(
                self.expense_line_item_service.repo,
                id=int(updated.id),
                qbo_id=qbo_line.qbo_line_id,
                # U-293-dw fold-in: fall back to the row's own already-stamped
                # realm_id when this call's realm_id is empty — an UPDATE touch
                # on a line that's already realm-complete must still re-stamp
                # QboId (e.g. QBO recycled the line id), not get skipped by
                # stamp_line_identity_or_warn's atomic-pair guard just because
                # this particular caller didn't have a fresh realm_id in hand.
                realm_id=realm_id or getattr(direct, "realm_id", None),
                context=f"Updated ExpenseLineItem {updated.id} ({path_label})",
                enforce_realm_pairing=True,
            )
            return updated

        # Memoized: qbo_line.id is fixed for this whole call, and both the fast
        # path (via resolve_mapping_state, on a MISSING/CONFLICT classification)
        # and the legacy path just below it (unconditionally, on a fast-path
        # miss) ask this exact same question — mirrors BillLineItemConnector's
        # identical memoization.
        _qbo_purchase_line_mapping_cache = {}

        def _read_by_qbo_purchase_line_id_cached(qbo_purchase_line_id):
            if qbo_purchase_line_id not in _qbo_purchase_line_mapping_cache:
                _qbo_purchase_line_mapping_cache[qbo_purchase_line_id] = (
                    self.mapping_repo.read_by_qbo_purchase_line_id(qbo_purchase_line_id)
                )
            return _qbo_purchase_line_mapping_cache[qbo_purchase_line_id]

        # U-293b: resolve identity directly against dbo.ExpenseLineItem's native
        # QboId, scoped to this line's own parent Expense (U-238b), before
        # falling back to the qbo.PurchaseLineExpenseLineItem mapping-table hop
        # below. Mirrors BillLineItemConnector's U-293 pilot exactly.
        # conflict->RAISE is structural (base.identity_fastpath.
        # run_line_identity_fastpath), never a fall-through to the legacy path.
        outcome = run_line_identity_fastpath(
            parent_local_id=expense_id,
            qbo_line_id=qbo_line.qbo_line_id,
            external_id=qbo_line.id,
            entity_label="ExpenseLineItem",
            external_label="QboPurchaseLine",
            read_direct_by_parent_and_qbo_line_id=self.expense_line_item_service.read_by_qbo_identity,
            read_by_local_id=self.mapping_repo.read_by_expense_line_item_id,
            read_by_external_id=_read_by_qbo_purchase_line_id_cached,
            external_id_attr="qbo_purchase_line_id",
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
                f"PurchaseLineExpenseLineItem identity conflict for QboPurchaseLine "
                f"{qbo_line.qbo_line_id} (id={qbo_line.id}) on Expense {expense_id}: "
                f"dbo.ExpenseLineItem {entity.id} already carries this identity but "
                f"the mapping table disagrees. Not auto-repointed; see the recorded "
                f"reconciliation issue. Skipping until a human resolves it."
            ),
            apply_fields=lambda direct: _apply_line_fields(direct, path_label="line fast path"),
        )
        if outcome.hit:
            return outcome.entity

        # Check for existing mapping. Memoized above — if the fast path already
        # asked this (MISSING/CONFLICT), this is a cache hit, not a second round trip.
        mapping = _read_by_qbo_purchase_line_id_cached(qbo_line.id)

        if not mapping:
            # Shape B fallback (task #17): content-fingerprint match when QBO
            # regenerates line IDs. Adopts an existing unmapped ExpenseLineItem
            # whose fields match this QBO line rather than creating a duplicate.
            orphan = self._find_and_match_by_fingerprint(
                expense_id=expense_id,
                description=qbo_line.description,
                amount=qbo_line.amount,
                qty=qbo_line.qty,
                rate=qbo_line.unit_price,
            )
            if orphan is not None:
                logger.info(
                    f"Adopting orphaned ExpenseLineItem {orphan.id} for QboPurchaseLine {qbo_line.id} "
                    f"via content fingerprint match"
                )
                try:
                    mapping = self.mapping_repo.create(
                        expense_line_item_id=int(orphan.id),
                        qbo_purchase_line_id=qbo_line.id,
                    )
                except Exception as error:
                    logger.warning(
                        f"Could not adopt orphaned ExpenseLineItem {orphan.id}: {error}"
                    )

        if mapping:
            # Found existing mapping - update the ExpenseLineItem
            line_item = self.expense_line_item_service.read_by_id(mapping.expense_line_item_id)
            if line_item:
                logger.debug(f"Updating existing ExpenseLineItem {line_item.id} from QboPurchaseLine {qbo_line.id}")
                # U-293b: reuse the SAME _apply_line_fields closure the fast path
                # uses (update + preserve-stored-value + identity re-stamp).
                return _apply_line_fields(line_item, path_label="legacy mapping-table path")
            else:
                # Mapping exists but ExpenseLineItem not found - recreate
                logger.warning(f"Mapping exists but ExpenseLineItem {mapping.expense_line_item_id} not found. Creating new.")
                self.mapping_repo.delete_by_id(mapping.id)
                mapping = None

        # Create new ExpenseLineItem
        logger.debug(f"Creating new ExpenseLineItem from QboPurchaseLine {qbo_line.id}")
        line_item = self.expense_line_item_service.create(
            expense_public_id=expense_public_id,
            sub_cost_code_id=sub_cost_code_id,
            project_public_id=project_public_id,
            description=qbo_line.description,
            quantity=default_qty,
            rate=default_rate,
            amount=qbo_line.amount,
            is_billable=is_billable,
            is_billed=is_billed,
            markup=default_markup,
            price=price,
            is_draft=False,
        )

        # Create mapping — if this fails we must roll back the line item we just created,
        # otherwise the unmapped line item will be duplicated on every subsequent sync run.
        line_item_id = coerce_id(line_item.id)
        try:
            mapping = self.create_mapping(expense_line_item_id=line_item_id, qbo_purchase_line_id=qbo_line.id)
            logger.debug(f"Created mapping: ExpenseLineItem {line_item_id} <-> QboPurchaseLine {qbo_line.id}")
        except Exception as e:
            try:
                self.expense_line_item_service.delete_by_public_id(line_item.public_id)
                logger.warning(
                    f"Rolled back orphan ExpenseLineItem {line_item_id} after mapping failure "
                    f"for QboPurchaseLine {qbo_line.id}"
                )
            except Exception as del_e:
                logger.error(f"Could not delete orphan ExpenseLineItem {line_item_id}: {del_e}")
            raise ValueError(
                f"Failed to create PurchaseLineExpenseLineItem mapping for QboPurchaseLine {qbo_line.id}: {e}"
            ) from e

        # U-238b: dbo line identity dual-write (create+update pairing for U-238c).
        # Mapping is already committed — a stamp failure must NOT roll back the line item.
        stamp_line_identity_or_warn(
            self.expense_line_item_service.repo,
            id=line_item_id,
            qbo_id=qbo_line.qbo_line_id,
            realm_id=realm_id,
            context=f"Created mapping for ExpenseLineItem {line_item_id} for QboPurchaseLine {qbo_line.id}",
            enforce_realm_pairing=True,
        )

        return line_item

    def _raise_line_identity_mapping_conflict_issue(
        self,
        *,
        qbo_line: QboPurchaseLine,
        dbo_line_id: int,
        local_side_mapping,
        qbo_side_mapping,
        realm_id: Optional[str] = None,
    ) -> None:
        """
        Record a dbo-identity <-> mapping-table split found by
        run_line_identity_fastpath's resolve_mapping_state. Mirrors
        BillLineItemConnector._raise_line_identity_mapping_conflict_issue
        exactly, scoped to the expense line level — covers all three conflict
        shapes (qbo-side only, local-side only, or both) in ONE issue, never
        silently dropping either side's blocker.
        """
        parts = [
            f"PurchaseLineExpenseLineItem identity conflict. dbo.ExpenseLineItem "
            f"{dbo_line_id} carries native QBO identity for QboPurchaseLine "
            f"{qbo_line.id} (QboLineId={qbo_line.qbo_line_id})."
        ]
        if qbo_side_mapping:
            parts.append(
                f"qbo-side: the mapping table still binds that same QboPurchaseLine to a "
                f"DIFFERENT ExpenseLineItem {qbo_side_mapping.expense_line_item_id} "
                f"(mapping {qbo_side_mapping.id})."
            )
        if local_side_mapping:
            parts.append(
                f"local-side: ExpenseLineItem {dbo_line_id}'s own mapping row (mapping "
                f"{local_side_mapping.id}) still binds it to a DIFFERENT QboPurchaseLine "
                f"{local_side_mapping.qbo_purchase_line_id}."
            )
        parts.append("Not auto-repointed — investigate which side is correct.")
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="expense_line_identity_conflict",
            entity_type="ExpenseLineItem",
            entity_public_id=None,
            qbo_id=str(qbo_line.qbo_line_id) if qbo_line.qbo_line_id else None,
            realm_id=realm_id or "",
            details=" ".join(parts),
        )

    def _get_sub_cost_code_id(self, qbo_item_ref_value: str, realm_id: Optional[str] = None) -> Optional[int]:
        """
        Get the SubCostCode ID from a QBO item reference value (U-307a: dbo-native
        SubCostCode.QboId first, legacy qbo.Item -> qbo.ItemSubCostCode hop on a
        miss — see cost_code_resolver.py), memoized for this connector's lifetime.

        Args:
            qbo_item_ref_value: QBO item reference value (QBO Item ID)
            realm_id: QBO realm, for the dbo-native lookup

        Returns:
            int: SubCostCode ID or None
        """
        if not qbo_item_ref_value:
            return None

        cache_key = (realm_id, qbo_item_ref_value)
        if cache_key in self._sub_cost_code_cache:
            return self._sub_cost_code_cache[cache_key]

        sub_cost_code = resolve_dbo_sub_cost_code(
            qbo_item_ref_value,
            realm_id,
            sub_cost_code_service=self.sub_cost_code_service,
        )
        if not sub_cost_code:
            logger.warning(f"No SubCostCode resolved for QBO Item ref '{qbo_item_ref_value}' — ExpenseLineItem will have no SubCostCode (billing gap)")
            self._sub_cost_code_cache[cache_key] = None
            return None

        self._sub_cost_code_cache[cache_key] = sub_cost_code.id
        return sub_cost_code.id

    # One of FOUR near-identical QBO customer-ref -> Project resolvers (invoice /
    # purchase / vendorcredit / bill). All four are realm-scoped as of U-060; they
    # still diverge on heal (invoice only) and caching (invoice + purchase only).
    # Lift into one shared resolver when multi-realm lands — see TODO.md.
    def _get_project_public_id(self, qbo_customer_ref_value: str, realm_id: Optional[str] = None) -> Optional[str]:
        """
        Get the Project public_id from QBO customer reference value.

        Args:
            qbo_customer_ref_value: QBO customer reference value (QBO Customer ID)
            realm_id: Optional QBO realm ID for realm-scoped customer lookup

        Returns:
            str: Project public_id or None
        """
        if not qbo_customer_ref_value:
            return None

        cache_key = (realm_id, qbo_customer_ref_value)

        if cache_key in self._project_cache:
            return self._project_cache[cache_key]

        # U-283b / U-276 §10 prereq: try dbo.Project's native QboId/RealmId
        # directly (mirrors U-283's bill_line_item repoint). Every Project
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
        # `docs/design/wave5.md` §2's "consequence worth flagging": a verify
        # disagreement used to degrade gracefully to the slower legacy hop;
        # once the mapping table's data source is gone it becomes
        # hard-stop-equivalent BY CONSTRUCTION, not by choice. Measured as a
        # no-op today (0 dbo<->mapping disagreements live), but a future
        # disagreement that used to degrade now resolves to None — the line
        # simply syncs without a Project binding, the safe side of that trade.
        direct_project = self.project_service.read_by_qbo_identity(qbo_customer_ref_value, realm_id)
        if direct_project:
            verified_qbo_id = verify_identity_dbo_only(
                direct_project,
                read_direct_by_qbo_identity=self.project_service.read_by_qbo_identity,
            )
            if verified_qbo_id:
                logger.debug(f"Found Project {direct_project.id} via direct dbo QboId lookup")
                self._project_cache[cache_key] = direct_project.public_id
                return direct_project.public_id

        self._project_cache[cache_key] = None
        return None

    def create_mapping(self, expense_line_item_id: int, qbo_purchase_line_id: int) -> PurchaseLineExpenseLineItem:
        """
        Create a mapping between ExpenseLineItem and QboPurchaseLine.

        Args:
            expense_line_item_id: Database ID of ExpenseLineItem record
            qbo_purchase_line_id: Database ID of QboPurchaseLine record

        Returns:
            PurchaseLineExpenseLineItem: The created mapping record

        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints
        existing_by_line_item = self.mapping_repo.read_by_expense_line_item_id(expense_line_item_id)
        if existing_by_line_item:
            raise ValueError(
                f"ExpenseLineItem {expense_line_item_id} is already mapped to QboPurchaseLine {existing_by_line_item.qbo_purchase_line_id}"
            )

        existing_by_qbo_line = self.mapping_repo.read_by_qbo_purchase_line_id(qbo_purchase_line_id)
        if existing_by_qbo_line:
            raise ValueError(
                f"QboPurchaseLine {qbo_purchase_line_id} is already mapped to ExpenseLineItem {existing_by_qbo_line.expense_line_item_id}"
            )

        # Create mapping
        return self.mapping_repo.create(expense_line_item_id=expense_line_item_id, qbo_purchase_line_id=qbo_purchase_line_id)

    # ------------------------------------------------------------------ #
    # Shape B line-matching helpers (task #17)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize_for_fingerprint(value) -> str:
        """Canonicalize a value for content-fingerprint comparison."""
        if value is None:
            return ""
        if isinstance(value, Decimal):
            return format(value.normalize(), "f")
        try:
            return format(Decimal(str(value)).normalize(), "f")
        except Exception:
            pass
        return str(value).strip()

    def _fingerprint_tuple(self, description, amount, qty, rate):
        return (
            self._normalize_for_fingerprint(description),
            self._normalize_for_fingerprint(amount),
            self._normalize_for_fingerprint(qty),
            self._normalize_for_fingerprint(rate),
        )

    def _find_and_match_by_fingerprint(
        self,
        *,
        expense_id: int,
        description,
        amount,
        qty,
        rate,
    ):
        """
        Find an unmapped ExpenseLineItem whose content fingerprint matches,
        POSITION-AWARE.

        Two tiers preserve pre-patch adoption while rescuing legacy NULL rows:
        - Tier 1 (exact): raw (description, amount, qty, rate) on target vs
          (description, amount, quantity, rate) on each candidate — no defaulting.
        - Tier 2 (normalized fallback): only if tier 1 found nothing, compare
          default_amount_only_line on both sides.

        Invariant: any row the pre-patch matcher would adopt is still adopted
        (tier 1); normalization only adds matches where tier 1 would find none.

        Tier 2 is a LEGACY-ROW SHIM, not a permanent feature: it exists only because
        rows created before U-098 stored NULL quantity/rate where the pull now stores
        1 x amount. Retire it (and collapse back to one tier) once no unmapped
        ExpenseLineItem on a QBO-sourced Expense still has NULL quantity/rate —
        otherwise it will be carried along into any future shared-matcher extraction.

        When several unmapped lines share a fingerprint within a tier, return the
        FIRST in stable position order (by id ≈ creation ≈ LineNum). The caller
        consumes it (creates a mapping) before the next QBO line, so processing lines
        in order pairs identical-content lines 1:1 by position — robust to QBO line-id
        regeneration even with duplicate content, instead of bailing and duplicating.
        Returns None only when nothing matches in either tier.
        """
        existing = self.expense_line_item_service.read_by_expense_id(expense_id=expense_id)
        unmapped = [
            li for li in sorted(existing, key=lambda c: int(getattr(c, "id", 0) or 0))
            if not self.mapping_repo.read_by_expense_line_item_id(int(li.id))
        ]

        def matches_for(tier_target, defaulted: bool):
            """Candidates whose fingerprint equals tier_target, in position order."""
            found = []
            for candidate in unmapped:
                cand_amount = getattr(candidate, "amount", None)
                cand_qty = getattr(candidate, "quantity", None)
                cand_rate = getattr(candidate, "rate", None)
                if defaulted:
                    cand_qty, cand_rate = default_amount_only_line(cand_qty, cand_rate, cand_amount)
                candidate_fp = self._fingerprint_tuple(
                    getattr(candidate, "description", None), cand_amount, cand_qty, cand_rate
                )
                if candidate_fp == tier_target:
                    found.append(candidate)
            return found

        def adopt(matches, tier_label):
            if len(matches) > 1:
                logger.info(
                    f"{len(matches)} unmapped ExpenseLineItems share the tier-{tier_label} "
                    f"fingerprint; adopting the first by position (QBO line-id regeneration)"
                )
            return matches[0]

        # Tier 1 — raw, exactly as the pre-patch matcher compared. Runs alone whenever
        # it hits, so the normalized pass costs nothing on the common path.
        exact = matches_for(self._fingerprint_tuple(description, amount, qty, rate), defaulted=False)
        if exact:
            return adopt(exact, "exact")

        # Tier 2 — legacy-row rescue only (see docstring).
        norm_qty, norm_rate = default_amount_only_line(qty, rate, amount)
        normalized = matches_for(
            self._fingerprint_tuple(description, amount, norm_qty, norm_rate), defaulted=True
        )
        if normalized:
            return adopt(normalized, "normalized")
        return None

    def get_mapping_by_expense_line_item_id(self, expense_line_item_id: int) -> Optional[PurchaseLineExpenseLineItem]:
        """
        Get mapping by ExpenseLineItem ID.
        """
        return self.mapping_repo.read_by_expense_line_item_id(expense_line_item_id)

    def get_mapping_by_qbo_purchase_line_id(self, qbo_purchase_line_id: int) -> Optional[PurchaseLineExpenseLineItem]:
        """
        Get mapping by QboPurchaseLine ID.
        """
        return self.mapping_repo.read_by_qbo_purchase_line_id(qbo_purchase_line_id)
