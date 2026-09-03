# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.bill.business.model import QboBillLine
from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository
from entities.bill_line_item.business.service import BillLineItemService
from entities.bill_line_item.business.model import BillLineItem
from entities.bill.business.service import BillService
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


class BillLineItemConnector:
    """
    Connector service for synchronization between QboBillLine and BillLineItem modules.

    U-363: dbo.BillLineItem.QboId/RealmId (U-238b), scoped to this line's own
    parent Bill, is the SOLE identity store — the qbo.BillLineItemBillLine
    mapping table is retired (U-349 program family 10/11, cloning U-361's
    shape; unlike U-362/U-362b/U-362c's InvoiceLineItem, BillLineItem carries
    no SourceType/LinkedTxn provenance, so none of that recognition logic
    applies here — see sync_from_qbo_bill_line's own docstring).
    """

    def __init__(
        self,
        bill_line_item_service: Optional[BillLineItemService] = None,
        bill_service: Optional[BillService] = None,
        bill_bill_repo=None,
        sub_cost_code_service: Optional[SubCostCodeService] = None,
        qbo_customer_repo: Optional[QboCustomerRepository] = None,
        customer_project_repo=None,
        project_service: Optional[ProjectService] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        """Initialize the BillLineItemConnector."""
        self.bill_line_item_service = bill_line_item_service or BillLineItemService()
        self.bill_service = bill_service or BillService()
        # U-355: never read anywhere in this file even before this unit (grep-
        # confirmed) -- BillBillRepository (its old type) is retired along with
        # qbo.BillBill, so this can no longer default-construct one. Kept as an
        # untyped, unconstructed constructor param (U-313/U-311/U-352 precedent)
        # so the existing test call sites across other units that still pass
        # bill_bill_repo= don't need to change for a unit whose real scope is
        # elsewhere.
        self.bill_bill_repo = bill_bill_repo
        # Cost-code resolution dep (U-307a; U-307d retired the legacy qbo.Item*
        # fallback repos) -- only passed to cost_code_resolver.resolve_dbo_sub_cost_code,
        # never used directly here.
        self.sub_cost_code_service = sub_cost_code_service
        # U-311: qbo_customer_repo/customer_project_repo are now DEAD -- the
        # legacy qbo.Customer -> qbo.CustomerProject hop that used them was
        # deleted from _resolve_project_public_id below (Wave 5 Option A).
        # U-314 dropped qbo.CustomerProject entirely, so customer_project_repo
        # can no longer default-construct its old repo class -- kept as an
        # untyped, unconstructed constructor param rather than removed: a
        # broad set of unrelated tests construct this connector defensively
        # passing every kwarg (mirrors U-313's own deliberate deferral of the
        # identical class of dead-DI-param cleanup for Bill/Purchase's
        # vendor_vendor_repo/qbo_vendor_repo -- see TODO.md). qbo_customer_repo's
        # own class (QboCustomerRepository) is untouched by this drop and
        # stays a live Pass-2/simplify candidate.
        self.qbo_customer_repo = qbo_customer_repo or QboCustomerRepository()
        self.customer_project_repo = customer_project_repo
        self.project_service = project_service or ProjectService()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()
        # Per-instance cache: a Bill's lines commonly share one job/customer_ref_value —
        # avoids re-resolving the identical (realm_id, qbo_customer_ref_value) pair once
        # per line. A fresh BillLineItemConnector is instantiated per Bill (see
        # BillBillConnector._sync_line_items), so this naturally scopes to one bill's
        # line-item sync, mirroring PurchaseLineExpenseLineItemConnector's _project_cache.
        self._project_cache: dict = {}

    def sync_from_qbo_bill_line(
        self,
        bill_id: int,
        qbo_bill_line: QboBillLine,
        live_qbo_line_ids: frozenset,
        realm_id: Optional[str] = None,
    ) -> BillLineItem:
        """
        Upsert a QBO BillLine into a BillLineItem, via the shared dbo-only line
        identity fast path (base/identity_fastpath.py::run_line_identity_fastpath_dbo_only
        — see its docstring for the create lock, the create-only MISS, and the
        stamp-rollback guarantee this connector relies on rather than
        re-implements):

          * HIT — a dbo row already carries `(bill_id, qbo_line_id)`: write the
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

        Raises on any projection failure; the parent fails the whole bill so
        the watermark holds and it retries.
        """
        if not qbo_bill_line.qbo_line_id:
            # Without a QBO Line.Id there is no dbo-native identity to resolve or
            # stamp, and no mapping row keyed on the staging PK to make an
            # unstamped line findable next pull — creating one would duplicate it
            # on every re-pull. QBO always assigns Line.Id on a persisted
            # transaction, so this is a fail-closed guard, not a path.
            raise ValueError(
                f"QboBillLine {qbo_bill_line.id} on Bill {bill_id} has no QBO "
                f"Line.Id - cannot resolve or stamp dbo-native line identity; skipping."
            )

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
        # QBO BillableStatus values: "Billable" (not yet invoiced), "HasBeenBilled" (already invoiced), "NotBillable" (not billable)
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

        def _apply_line_fields(direct: BillLineItem) -> Optional[BillLineItem]:
            """
            `apply_fields` for the HIT branch: write the QBO-derived fields onto
            the dbo-identity-matched BillLineItem. Returns None on a concurrent-
            DELETE `update_by_public_id` miss, mirroring the header connector's
            own `_apply_bill_fields` — the helper's `_apply()` raises
            `raise_concurrent_write_race` unconditionally on a None return, so
            that ONE raise stays the single place the guarantee lives. A true
            ROWVERSION conflict instead propagates `DatabaseConcurrencyError`
            straight out of this closure, still caught by the header
            connector's `_sync_line_items` generic handler and still
            failing/retrying the bill the same way.

            Identity itself is NOT unconditionally re-stamped here (U-361's
            shape) — the row was found BY that identity; re-stamping every
            touch was the mapping era's dual-write. A one-off realm self-heal
            below is the exception: a legacy row can be stamped with a QboId
            but no RealmId (a partial historical stamp), and a fast-path HIT
            that skipped healing it would find such a row forever afterward
            and never correct it — the same live-prod gap U-293's Gate-2
            equivalence check found for the header-level fast path.
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
                    f"(concurrent write race)"
                )
                raise_concurrent_write_race(
                    entity_label="BillLineItem", entity_id=direct.id, path_label="line fast path"
                )
            if realm_id and not getattr(direct, "realm_id", None):
                # Legacy realm gap (U-293-dw): the row was found by its QboId but
                # never got the RealmId half of the atomic pair. Heal it once,
                # best-effort — a failure here must not fail the line (the row
                # is still correctly identified by (BillId, QboId)).
                stamp_line_identity_or_warn(
                    self.bill_line_item_service.repo,
                    id=coerce_id(updated.id),
                    qbo_id=qbo_bill_line.qbo_line_id,
                    realm_id=realm_id,
                    context=f"Updated BillLineItem {updated.id} (realm self-heal)",
                    enforce_realm_pairing=True,
                )
            return updated

        def _readopt_stale_line() -> Optional[BillLineItem]:
            """
            `readopt_candidate` for the MISS branch (U-361b's shape): find a
            local line under this Bill whose current identity is stale (not in
            `live_qbo_line_ids`) and whose (description, amount, qty, rate)
            fingerprint matches this QBO line. Matches the pre-U-363
            `_match_by_fingerprint` selection exactly — same fingerprint
            fields, same stable position-order pick — via the shared
            `find_stale_identity_orphan` (base/line_orphan_adopt.py), the same
            matcher U-361/U-362 use. Pure lookup: no field writes here — the
            primitive applies `apply_fields` to whatever this returns before
            stamping.
            """
            existing = self.bill_line_item_service.read_by_bill_id(bill_id)
            return find_stale_identity_orphan(
                existing_lines=existing,
                live_qbo_line_ids=live_qbo_line_ids,
                fingerprint=lambda li: (
                    self._fingerprint(li.description), self._fingerprint(li.amount),
                    self._fingerprint(li.quantity), self._fingerprint(li.rate),
                ),
                target=(
                    self._fingerprint(description), self._fingerprint(amount),
                    self._fingerprint(qty), self._fingerprint(rate),
                ),
            )

        def _create_line() -> Optional[BillLineItem]:
            """
            `resolve_candidate` for the MISS branch: create the line fresh (no
            adopt — see the method docstring). Fails closed BEFORE creating when
            the realm needed for the stamp is missing: SetBillLineItemQboIdentity's
            own atomic-pair guard declines to write a QboId without a RealmId, so
            the line would come out unstamped — which post-U-363 means
            unfindable, i.e. re-created as a duplicate on every pull. The helper
            would catch the un-landed stamp and roll the row back anyway;
            refusing up front just saves the create+delete round trip.
            """
            if not realm_id:
                raise RuntimeError(
                    f"Refusing to create BillLineItem for QboBillLine "
                    f"{qbo_bill_line.qbo_line_id} on Bill {bill_id}: realm_id is "
                    f"missing, so its dbo-native identity stamp could not land and "
                    f"the line would be an unfindable orphan. Holding for retry."
                )
            return self.bill_line_item_service.create(
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
            )

        def _stamp_line_identity(candidate: BillLineItem) -> Optional[BillLineItem]:
            """
            `stamp_identity` for the MISS branch: the bare dbo-native stamp, then
            a re-read — `set_qbo_identity` is a void DB write that never mutates
            `candidate` in memory, and the helper verifies the returned row
            actually carries the identity (its "stamp did not land" guard).
            """
            self.bill_line_item_service.repo.set_qbo_identity(
                id=coerce_id(candidate.id), qbo_id=qbo_bill_line.qbo_line_id, realm_id=realm_id,
            )
            return self.bill_line_item_service.read_by_id(coerce_id(candidate.id))

        def _rollback_line(candidate: BillLineItem) -> None:
            """
            `rollback_candidate` for the MISS branch (U-354/U-355's identity-
            stamp rollback, at line level): best-effort delete of the just-
            created, unstamped line. `rollback_orphan_header` is the shared
            compensating-delete mechanism (not header-specific beyond its name);
            `delete_mapping` is a no-op because there is no mapping row. A
            failed delete leaves an unstamped orphan that inflates this bill's
            local lines on every future pull, so it is recorded to
            reconciliation monitoring exactly as the header connector records
            its own orphan header.
            """
            rollback_orphan_header(
                delete_header=lambda: self.bill_line_item_service.delete_by_public_id(
                    candidate.public_id
                ),
                delete_mapping=lambda: None,
                entity_label="BillLineItem",
                entity_id=candidate.id,
                on_header_delete_failed=lambda exc: record_orphan_line_issue(
                    self.reconciliation_repo,
                    drift_type="orphan_bli_line_item",
                    entity_type="BillLineItem",
                    line_item=candidate,
                    qbo_line_id=qbo_bill_line.qbo_line_id,
                    parent_label="Bill",
                    parent_id=bill_id,
                    realm_id=realm_id,
                    exc=exc,
                ),
            )

        outcome = run_line_identity_fastpath_dbo_only(
            parent_local_id=bill_id,
            qbo_line_id=qbo_bill_line.qbo_line_id,
            entity_label="BillLineItem",
            external_label="QboBillLine",
            lock_resource_label="BillLineItem",
            read_direct_by_parent_and_qbo_line_id=self.bill_line_item_service.read_by_qbo_identity,
            readopt_candidate=_readopt_stale_line,
            resolve_candidate=_create_line,
            stamp_identity=_stamp_line_identity,
            rollback_candidate=_rollback_line,
            apply_fields=_apply_line_fields,
            on_readopt_stamp_failed=lambda readopted, exc: record_readopt_stamp_failed_issue(
                self.reconciliation_repo,
                drift_type="bli_line_readopt_failed",
                entity_type="BillLineItem",
                line_item=readopted,
                qbo_line_id=qbo_bill_line.qbo_line_id,
                parent_label="Bill",
                parent_id=bill_id,
                realm_id=realm_id,
                exc=exc,
            ),
            on_create_failed=lambda exc: record_create_failed_issue(
                self.reconciliation_repo,
                drift_type="bli_line_create_failed",
                entity_type="BillLineItem",
                qbo_line_id=qbo_bill_line.qbo_line_id,
                parent_label="Bill",
                parent_id=bill_id,
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
        Mirrors the pre-U-363 `_normalize_for_fingerprint`."""
        if value is None:
            return ""
        if isinstance(value, Decimal):
            return format(value.normalize(), "f")
        try:
            return format(Decimal(str(value)).normalize(), "f")
        except Exception:
            return str(value).strip()

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
