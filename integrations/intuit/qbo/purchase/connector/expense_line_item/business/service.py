# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
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
    run_line_identity_fastpath_dbo_only,
)
from integrations.intuit.qbo.base.line_orphan_adopt import find_stale_identity_orphan
from integrations.intuit.qbo.base.compensation import rollback_orphan_header
from integrations.intuit.qbo.base.cost_code_resolver import resolve_dbo_sub_cost_code
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.line_orphan_recorder import (
    record_create_failed_issue,
    record_orphan_line_issue,
    record_readopt_stamp_failed_issue,
)
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

    U-364: dbo.ExpenseLineItem.QboId/RealmId (U-238b), scoped to this line's own
    parent Expense, is the SOLE identity store — the qbo.PurchaseLineExpenseLineItem
    mapping table is retired (U-349 program family 11/11, LAST family, cloning
    U-363's bill_line_item shape). Unlike Bill, Expense carries expense-specific
    field-decision helpers (default_amount_only_line / preserve_stored_value, for
    Ramp amount-only card-spend lines) which are unaffected by the identity repoint
    and are kept unchanged below.
    """

    def __init__(
        self,
        expense_line_item_service: Optional[ExpenseLineItemService] = None,
        sub_cost_code_service: Optional[SubCostCodeService] = None,
        customer_project_repo=None,
        qbo_customer_repo: Optional[QboCustomerRepository] = None,
        project_service: Optional[ProjectService] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        """Initialize the PurchaseLineExpenseLineItemConnector."""
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
        # U-364: qbo.PurchaseLineExpenseLineItem's mapping repo/model/create_mapping/
        # fingerprint-adopt are retired -- dbo.ExpenseLineItem.QboId/RealmId (U-238b)
        # is the sole identity store going forward. Unlike customer_project_repo/
        # qbo_customer_repo above (dead from an EARLIER, unrelated retirement with
        # real existing callers to stay compatible with), no live caller anywhere
        # in the repo ever constructs this connector with a mapping_repo= kwarg
        # (grep-confirmed), so — mirroring BillLineItemConnector's own U-363
        # precedent exactly — the param is dropped outright rather than kept as
        # dead DI.

    def sync_from_qbo_purchase_line(
        self,
        expense_id: int,
        expense_public_id: str,
        qbo_line: QboPurchaseLine,
        live_qbo_line_ids: frozenset,
        realm_id: Optional[str] = None,
    ) -> ExpenseLineItem:
        """
        Upsert a QBO PurchaseLine into an ExpenseLineItem, via the shared dbo-only
        line identity fast path (base/identity_fastpath.py::
        run_line_identity_fastpath_dbo_only — see its docstring for the create
        lock, the create-only MISS, and the stamp-rollback guarantee this
        connector relies on rather than re-implements):

          * HIT — a dbo row already carries `(expense_id, qbo_line_id)`: write the
            QBO-derived fields onto it in place.
          * MISS — re-adopt before create (U-361b's shape). QBO regenerates a
            line's `Line.Id` on certain edits with its content unchanged; a
            genuine MISS first looks for a local line under this parent whose
            CURRENT identity is no longer in `live_qbo_line_ids` (this pull's
            live line-id set) AND whose content fingerprint matches the
            incoming line — a "stale-identity orphan" — and re-stamps THAT row
            (reusing its dbo.Id, its attachments, any InvoiceLineItem FK)
            instead of minting a sibling. Only when nothing matches does a
            fresh line get created, then stamped with the bare
            `set_qbo_identity`. A stamp that raises or does not land is rolled
            back by the helper and re-raised — an unstamped line has no
            mapping row left to make it findable on the next pull (it would be
            re-created as a duplicate every pull), so a best-effort stamp is
            not acceptable.

        Raises on any projection failure; the parent fails the whole expense so
        the watermark holds and it retries.
        """
        if not qbo_line.qbo_line_id:
            # Without a QBO Line.Id there is no dbo-native identity to resolve or
            # stamp, and no mapping row keyed on the staging PK to make an
            # unstamped line findable next pull — creating one would duplicate it
            # on every re-pull. QBO always assigns Line.Id on a persisted
            # transaction, so this is a fail-closed guard, not a path.
            raise ValueError(
                f"QboPurchaseLine {qbo_line.id} on Expense {expense_id} has no QBO "
                f"Line.Id - cannot resolve or stamp dbo-native line identity; skipping."
            )

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

        description = qbo_line.description
        amount = qbo_line.amount

        # What this pull WOULD write if the local row carried nothing (U-098). A QBO
        # amount-only line (Ramp card spend on 58999) has no Qty/UnitPrice/MarkupInfo
        # at all, which used to persist as NULL quantity/rate/markup. Derived once
        # per line; the create and update paths below decide whether to apply them.
        # This is also what `create()` persists for an amount-only line — so the
        # readopt fingerprint below matches against these DEFAULTED values, not the
        # raw (possibly-None) qbo_line.qty/unit_price.
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

        def _apply_line_fields(direct: ExpenseLineItem) -> Optional[ExpenseLineItem]:
            """
            `apply_fields` for the HIT branch: write the QBO-derived fields onto
            the dbo-identity-matched ExpenseLineItem. Returns None on a concurrent-
            DELETE `update_by_public_id` miss — the primitive's `_apply()` raises
            `raise_concurrent_write_race` unconditionally on a None return, so
            that ONE raise stays the single place the guarantee lives.

            Identity itself is NOT unconditionally re-stamped here (U-364's
            shape, mirroring U-363's bill_line_item) — the row was found BY that
            identity; re-stamping every touch was the mapping era's dual-write. A
            one-off realm self-heal below is the exception: a legacy row can be
            stamped with a QboId but no RealmId (a partial historical stamp), and
            a fast-path HIT that skipped healing it would find such a row
            forever afterward and never correct it.
            """
            update_qty = preserve_stored_value(default_qty, qbo_line.qty, direct.quantity)
            update_rate = preserve_stored_value(default_rate, qbo_line.unit_price, direct.rate)
            update_markup = preserve_stored_value(default_markup, qbo_line.markup_percent, direct.markup)

            updated = self.expense_line_item_service.update_by_public_id(
                direct.public_id,
                row_version=direct.row_version,
                sub_cost_code_id=sub_cost_code_id,
                project_public_id=project_public_id,
                description=description,
                quantity=update_qty,
                rate=update_rate,
                amount=amount,
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
                    f"(concurrent write race)"
                )
                raise_concurrent_write_race(
                    entity_label="ExpenseLineItem", entity_id=direct.id, path_label="line fast path"
                )
            if realm_id and not getattr(direct, "realm_id", None):
                # Legacy realm gap (U-293-dw): the row was found by its QboId but
                # never got the RealmId half of the atomic pair. Heal it once,
                # best-effort — a failure here must not fail the line (the row
                # is still correctly identified by (ExpenseId, QboId)).
                stamp_line_identity_or_warn(
                    self.expense_line_item_service.repo,
                    id=coerce_id(updated.id),
                    qbo_id=qbo_line.qbo_line_id,
                    realm_id=realm_id,
                    context=f"Updated ExpenseLineItem {updated.id} (realm self-heal)",
                    enforce_realm_pairing=True,
                )
            return updated

        def _readopt_stale_line() -> Optional[ExpenseLineItem]:
            """
            `readopt_candidate` for the MISS branch (U-361b's shape): find a
            local line under this Expense whose current identity is stale (not
            in `live_qbo_line_ids`) and whose (description, amount, qty, rate)
            fingerprint matches this QBO line, via the shared
            `find_stale_identity_orphan` (base/line_orphan_adopt.py), the same
            matcher U-361/U-362/U-363 use. Single-tier — the pre-U-364 two-tier
            `_find_and_match_by_fingerprint` shim (raw-exact first, normalized
            fallback second, to prefer an exact match over a coincidentally-
            normalized sibling) is retired — but BOTH sides of the comparison
            run through `default_amount_only_line` before fingerprinting, not
            just the target: a stale candidate already carrying the defaulted
            quantity=1/rate=amount shape `create()` persists is unaffected (the
            defaulting is a no-op once both fields are already set), but a
            LEGACY pre-U-098 orphan whose quantity/rate are still NULL (created
            before the amount-only default existed, then went stale before ever
            being re-synced in place to self-heal) now normalizes to the SAME
            defaulted shape as the incoming line instead of silently missing
            the match and minting a duplicate (Codex Gate-2 P2, U-364 round 2).
            Pure lookup: no field writes here — the primitive applies
            `apply_fields` to whatever this returns before stamping.
            """
            existing = self.expense_line_item_service.read_by_expense_id(expense_id=expense_id)

            def _fp(desc, amt, qty, rate) -> tuple:
                return (
                    self._fingerprint(desc), self._fingerprint(amt),
                    self._fingerprint(qty), self._fingerprint(rate),
                )

            def _candidate_fingerprint(li) -> tuple:
                cand_qty, cand_rate = default_amount_only_line(li.quantity, li.rate, li.amount)
                return _fp(li.description, li.amount, cand_qty, cand_rate)

            return find_stale_identity_orphan(
                existing_lines=existing,
                live_qbo_line_ids=live_qbo_line_ids,
                fingerprint=_candidate_fingerprint,
                target=_fp(description, amount, default_qty, default_rate),
            )

        def _create_line() -> Optional[ExpenseLineItem]:
            """
            `resolve_candidate` for the MISS branch: create the line fresh (no
            adopt — see the method docstring). Fails closed BEFORE creating when
            the realm needed for the stamp is missing: SetExpenseLineItemQboIdentity's
            own atomic-pair guard declines to write a QboId without a RealmId, so
            the line would come out unstamped — which post-U-364 means
            unfindable, i.e. re-created as a duplicate on every pull. The helper
            would catch the un-landed stamp and roll the row back anyway;
            refusing up front just saves the create+delete round trip.
            """
            if not realm_id:
                raise RuntimeError(
                    f"Refusing to create ExpenseLineItem for Expense {expense_id}: "
                    f"realm_id is missing, so its dbo-native identity stamp could not "
                    f"land and the line would be an unfindable orphan. Holding for retry."
                )
            return self.expense_line_item_service.create(
                expense_public_id=expense_public_id,
                sub_cost_code_id=sub_cost_code_id,
                project_public_id=project_public_id,
                description=description,
                quantity=default_qty,
                rate=default_rate,
                amount=amount,
                is_billable=is_billable,
                is_billed=is_billed,
                markup=default_markup,
                price=price,
                is_draft=False,
            )

        def _stamp_line_identity(candidate: ExpenseLineItem) -> Optional[ExpenseLineItem]:
            """
            `stamp_identity` for the MISS branch: the bare dbo-native stamp, then
            a re-read — `set_qbo_identity` is a void DB write that never mutates
            `candidate` in memory, and the helper verifies the returned row
            actually carries the identity (its "stamp did not land" guard).
            """
            self.expense_line_item_service.repo.set_qbo_identity(
                id=coerce_id(candidate.id), qbo_id=qbo_line.qbo_line_id, realm_id=realm_id,
            )
            return self.expense_line_item_service.read_by_id(coerce_id(candidate.id))

        def _rollback_line(candidate: ExpenseLineItem) -> None:
            """
            `rollback_candidate` for the MISS branch (U-354/U-355/U-363's
            identity-stamp rollback, at line level): best-effort delete of the
            just-created, unstamped line. `rollback_orphan_header` is the shared
            compensating-delete mechanism (not header-specific beyond its name);
            `delete_mapping` is a no-op because there is no mapping row. A
            failed delete leaves an unstamped orphan that inflates this
            expense's local lines on every future pull, so it is recorded to
            reconciliation monitoring exactly as the header connector records
            its own orphan header.
            """
            rollback_orphan_header(
                delete_header=lambda: self.expense_line_item_service.delete_by_public_id(
                    candidate.public_id
                ),
                delete_mapping=lambda: None,
                entity_label="ExpenseLineItem",
                entity_id=candidate.id,
                on_header_delete_failed=lambda exc: record_orphan_line_issue(
                    self.reconciliation_repo,
                    drift_type="orphan_eli_line_item",
                    entity_type="ExpenseLineItem",
                    line_item=candidate,
                    qbo_line_id=qbo_line.qbo_line_id,
                    parent_label="Expense",
                    parent_id=expense_id,
                    realm_id=realm_id,
                    exc=exc,
                ),
            )

        outcome = run_line_identity_fastpath_dbo_only(
            parent_local_id=expense_id,
            qbo_line_id=qbo_line.qbo_line_id,
            entity_label="ExpenseLineItem",
            external_label="QboPurchaseLine",
            lock_resource_label="ExpenseLineItem",
            read_direct_by_parent_and_qbo_line_id=self.expense_line_item_service.read_by_qbo_identity,
            readopt_candidate=_readopt_stale_line,
            resolve_candidate=_create_line,
            stamp_identity=_stamp_line_identity,
            rollback_candidate=_rollback_line,
            apply_fields=_apply_line_fields,
            on_readopt_stamp_failed=lambda readopted, exc: record_readopt_stamp_failed_issue(
                self.reconciliation_repo,
                drift_type="eli_line_readopt_failed",
                entity_type="ExpenseLineItem",
                line_item=readopted,
                qbo_line_id=qbo_line.qbo_line_id,
                parent_label="Expense",
                parent_id=expense_id,
                realm_id=realm_id,
                exc=exc,
            ),
            on_create_failed=lambda exc: record_create_failed_issue(
                self.reconciliation_repo,
                drift_type="eli_line_create_failed",
                entity_type="ExpenseLineItem",
                qbo_line_id=qbo_line.qbo_line_id,
                parent_label="Expense",
                parent_id=expense_id,
                realm_id=realm_id,
                exc=exc,
            ),
        )
        # qbo_line_id is guaranteed truthy above, so the helper's only hit=False
        # outcome is unreachable here and hit=True never carries entity=None.
        return outcome.entity

    @staticmethod
    def _fingerprint(value) -> str:
        """Canonicalize a value for content-fingerprint comparison (10 == 10.00).
        Mirrors bill_line_item's own `_fingerprint` (U-363), which itself mirrors
        this connector's pre-U-364 `_normalize_for_fingerprint`."""
        if value is None:
            return ""
        if isinstance(value, Decimal):
            return format(value.normalize(), "f")
        try:
            return format(Decimal(str(value)).normalize(), "f")
        except Exception:
            return str(value).strip()

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
