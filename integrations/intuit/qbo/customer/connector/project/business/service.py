# Python Standard Library Imports
import logging
from typing import Iterator, Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.base.field_ownership import (
    preserve_human_edited_name,
    raise_if_inactive_unmapped,
)
from integrations.intuit.qbo.base.identity_consistency import verify_identity_dbo_only
from integrations.intuit.qbo.base.identity_fastpath import (
    run_identity_fastpath_dbo_only,
    stamp_dbo_identity_with_lock,
)
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.customer.business.model import QboCustomer
from integrations.intuit.qbo.customer.connector.customer.business.service import (
    address_fields_are_blank,
    billing_address_qbo_id,
)
from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository
from integrations.intuit.qbo.physical_address.connector.business.service import PhysicalAddressAddressConnector
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from integrations.intuit.qbo.base.reconciliation_recorder import (
    build_duplicate_qbo_identity_conflict_desc,
    record_duplicate_identity_conflict,
    record_mapping_issue,
)
from entities.customer.business.service import CustomerService
from entities.project.business.service import ProjectService
from entities.project.business.model import Project
from entities.project_address.business.service import ProjectAddressService
from entities.address.business.service import AddressService

logger = logging.getLogger(__name__)

# Address type IDs (these would typically come from a lookup table)
ADDRESS_TYPE_BILLING = 1
ADDRESS_TYPE_SHIPPING = 2

# ── U-506 P1: what a Project's BILLING address slot MEANS ────────────────────
# The ONE place this semantic lives. Flipping it is a one-line change; nothing
# else in this module encodes the choice.
#
# ✅ DECIDED by Chris, 2026-09-23: the slot is the OWNER'S MAILING address.
#    This shipped as an EM assumption and is now a confirmed product decision —
#    do not re-litigate it as "an assumption" on a later pass. The False branch
#    below is retained because it documents what the decision RULED OUT and what
#    breaks if someone flips it, not because the question is still open.
#
#   True  — the OWNER'S MAILING / REMIT-TO address   ← SHIPPED + DECIDED
#       Evidence: `entities/invoice/business/g702.py` renders this block under
#       the literal label "TO OWNER:" and renders the property SEPARATELY as
#       "PROJECT:" from `project.name`; the Draw Request "To:" name is
#       `customer.name`. Pairing a customer's NAME with a property STREET is
#       incoherent. Under this reading the parent customer's BillAddr IS the
#       owner's mailing address, so inheriting it is correct for all 61
#       projects the fallback newly covers — `c/o` billing agents included.
#
#   False — the PROPERTY (street) address of the job itself
#       Under this reading inheritance is WRONG for 16 of those 61: the
#       parent's address is then a SIBLING project's street (e.g.
#       `BD - 4527 Beacon Dr.` would inherit `1539 Old Hillsboro Road - OHR2`).
#       Setting False degrades the billing chain to own-bill ONLY and reads no
#       parent row. (Before U-506 P2 this said "own-bill → own-ship". That was
#       written when own-ship was still a billing candidate; it no longer is,
#       under either reading — a job's ShipAddr is the SITE. See
#       `_billing_address_candidates`.)
#
# Either way the SHIPPING slot stays job-only (see `_sync_addresses`): it never
# inherits, and it is the only place a future property-address feature belongs.
PROJECT_BILLING_ADDRESS_IS_OWNER_MAILING = True


# ── U-513 ph2.5: the SHIPPING slot's synthetic `dbo.Address` identity ────────
# The billing twin (`billing_address_qbo_id`) lives in `CustomerCustomerConnector`
# because BOTH halves of the family write and read it. `_ship` has no such
# sharing problem and deliberately gets none: a PARENT customer never projects a
# shipping address (shipping is job-only and never inherits — see
# `_sync_addresses`), so this connector is the ONLY writer and the ONLY reader of
# the `_ship` identity. Homing it here keeps that ownership visible.
SHIPPING_ADDRESS_QBO_ID_SUFFIX = "_ship"


def shipping_address_qbo_id(customer_qbo_id: str) -> str:
    """
    The SYNTHETIC `dbo.Address` identity for a QBO job customer's ShipAddr.

    Byte-identical to the string `QboCustomerService._upsert_customer` has always
    written onto the `qbo.PhysicalAddress` staging row (`f"{id}_ship"`), which is
    what `SetAddressQboIdentity` then stamped onto `dbo.Address`. Keeping it
    unchanged is what lets the payload path match the EXISTING dbo.Address rows
    instead of re-keying them — the same no-migration guarantee the `_bill`
    identity carries.
    """
    return f"{customer_qbo_id}{SHIPPING_ADDRESS_QBO_ID_SUFFIX}"


class CustomerProjectConnector:
    """
    Connector service for synchronization between QboCustomer and Project modules.
    Handles job/sub-customer QBO Customers (Job=true) mapping to Project.
    
    Also syncs addresses from QboPhysicalAddress to Address via ProjectAddress.
    """

    def __init__(
        self,
        project_service: Optional[ProjectService] = None,
        project_address_service: Optional[ProjectAddressService] = None,
        address_connector: Optional[PhysicalAddressAddressConnector] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
        customer_service: Optional[CustomerService] = None,
        qbo_customer_repo: Optional[QboCustomerRepository] = None,
        address_service: Optional[AddressService] = None,
    ):
        """Initialize the CustomerProjectConnector."""
        self.project_service = project_service or ProjectService()
        self.project_address_service = project_address_service or ProjectAddressService()
        self.address_connector = address_connector or PhysicalAddressAddressConnector()
        # U-506 P2: reads dbo.Address.qbo_id to tell a CONNECTOR-MINTED billing
        # link from a HAND-SET one. Only the former may be cleared as stale.
        self.address_service = address_service or AddressService()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()
        # ⚠️ U-513: DEAD DI PARAM AGAIN. History: U-297 fed
        # _resolve_parent_customer_id from this repo; U-310 repointed that
        # resolver onto Option A (dbo-only verify) and U-311 repointed this
        # connector's OWN pull onto Option B, which left the param unused;
        # U-506 P1 revived it for `_get_parent_qbo_customer`, which read the
        # parent's qbo.Customer STAGING row for its BillAddrId. U-513 deleted
        # that read — the parent's address now comes from `dbo.Address` via
        # `_parent_billing_address_id`, so NOTHING in this class touches
        # qbo.Customer staging any more. The param is retained (not removed)
        # only because six other units' test files construct this connector
        # with it; dropping it is a clean standalone follow-up, not this unit's
        # business. (The sibling customer_mapping_repo param --
        # CustomerCustomerRepository, dropped by U-314 -- was removed outright
        # rather than neutered: unlike vendor_vendor_repo/customer_project_repo
        # on other connectors, its only 5 test call sites are all in that unit's
        # own touched files.)
        self.customer_service = customer_service or CustomerService()
        self.qbo_customer_repo = qbo_customer_repo or QboCustomerRepository()
        # Per-instance memo for the parent-Customer resolution. ONE connector
        # instance serves every job customer in a pull run, and sub-units of one
        # property share a parent_ref_value — 136 job customers resolve to only
        # 71 distinct parents in prod (live count 2026-08-22), so this is what
        # keeps the extra direct+verify reads a net round-trip WIN over the
        # legacy two-hop rather than a 50% regression. Run-scoped by
        # construction: a fresh connector per pull run, so nothing survives a
        # tick. Caches misses as well as hits, per the canonical shape.
        self._parent_customer_cache: dict = {}
        # U-506 P1 / U-513: the parent's inherited BILLING `dbo.Address` id,
        # memoized on the SAME (realm_id, parent_ref_value) key and for the same
        # reason — 138 job customers resolve to only 73 distinct parents (live
        # count 2026-09-23), so the address fallback costs ~73 reads per run
        # rather than one per job. U-513 changed WHAT is memoized: the resolved
        # dbo.Address id (or None), not the parent's staging row — the blankness
        # verdict is part of the memo now, so a blank parent costs one read per
        # run instead of one per job. Run-scoped by construction (a fresh
        # connector per pull run) and caches MISSES as well as hits, per the
        # canonical shape above.
        self._parent_billing_address_cache: dict = {}

    def sync_from_qbo_customer(
        self,
        qbo_customer: QboCustomer,
        external_customer=None,
    ) -> Project:
        """
        Sync data from QboCustomer to Project module, via the dbo-only identity
        fast path (U-311 — Wave-5 Option B, mirrors U-310's
        `CustomerCustomerConnector` / U-313's `VendorVendorConnector`).

        Args:
            qbo_customer: QboCustomer record (must be a job/sub-customer with Job=true)
            external_customer: the ORIGINAL QBO Customer payload this staging row
                was built from (`customer/external/schemas.py::QboCustomer`), when
                the caller still has it in scope (U-513 ph2.5). Optional and
                defaulted to None so every existing call site keeps working
                untouched; it carries the INLINE `BillAddr` AND `ShipAddr` this
                connector projects instead of the `qbo.PhysicalAddress` staging
                rows. See `_sync_addresses` for what happens when it is absent.

        Returns:
            Project: The synced Project record

        Raises:
            ValueError: If the customer has Job=false (is not a job/sub-customer)
            ValueError: On a detected duplicate QBO sub-customer (name-matched local
                Project already carrying a DIFFERENT QBO identity)
        """
        if not qbo_customer.is_job:
            raise ValueError(f"QboCustomer {qbo_customer.id} has Job=false and is not a job/sub-customer")

        # Map QBO Customer fields to Project module fields
        project_name = qbo_customer.display_name or qbo_customer.company_name or ""
        project_description = qbo_customer.notes or ""
        project_status = "active" if qbo_customer.active else "inactive"

        # Find the parent Customer ID if this job has a parent (U-297 — the
        # empty-parent_ref_value guard now lives inside the resolver).
        customer_id = self._get_parent_customer_id(
            qbo_customer.parent_ref_value, qbo_customer.realm_id
        )

        outcome = run_identity_fastpath_dbo_only(
            qbo_id=qbo_customer.qbo_id,
            realm_id=qbo_customer.realm_id,
            entity_label="Project",
            external_label="QboCustomer",
            lock_resource_label="Project",
            read_direct_by_qbo_identity=self.project_service.read_by_qbo_identity,
            apply_fields=lambda entity: self._apply_project_fields_and_sync(
                entity,
                qbo_customer=qbo_customer,
                name=project_name,
                description=project_description,
                status=project_status,
                customer_id=customer_id,
                external_customer=external_customer,
            ),
            resolve_candidate=lambda: self._resolve_project_candidate(
                qbo_customer,
                name=project_name,
                description=project_description,
                status=project_status,
                customer_id=customer_id,
            ),
            stamp_identity=lambda candidate: self._stamp_project_identity(
                candidate, qbo_customer, customer_id=customer_id,
                external_customer=external_customer,
            ),
        )
        if outcome.entity is None:
            # U-316: no longer race-reachable (see run_identity_fastpath_
            # dbo_only's Raises docstring) — kept as a backstop for a
            # directly-invoked falsy qbo_customer.qbo_id (this public method has
            # no guard of its own; pinned by test_project_no_qbo_id_raises).
            # The production pull path already guards this upstream via
            # QboCustomerService._upsert_customer (U-336).
            raise RuntimeError(
                f"Failed to resolve Project for QboCustomer {qbo_customer.id} "
                f"(qbo_id={qbo_customer.qbo_id}) via the dbo-only identity fast path"
            )
        return outcome.entity

    def _resolve_project_candidate(
        self,
        qbo_customer: QboCustomer,
        *,
        name: str,
        description: str,
        status: str,
        customer_id: Optional[int],
    ) -> Project:
        """
        `resolve_candidate` for the dbo-only fast path's MISS branch (U-311):
        called only under `run_identity_fastpath_dbo_only`'s create lock, once
        a genuine miss is confirmed (no dbo.Project currently holds this
        identity, including the re-read under lock). Adopts an existing local
        Project by exact (case-insensitive — SQL Server default collation)
        NAME match first — the original-import-time gap where dbo.Project
        rows exist with no paired mapping (10 of 11 known dup-set names as of
        2026-05-28, see docs/dedupe-project-rows.md) — before falling through
        to a fresh create. Mirrors `_resolve_customer_candidate`'s (U-310)
        shape exactly; the old heal-in-place-a-stale-mapping branch has no
        counterpart here — that branch existed only because a SECOND,
        independently-writable store (the mapping table) could point at a
        missing row; dbo-only mode has no second store left to go stale.
        """
        raise_if_inactive_unmapped(
            qbo_customer.active, qbo_label="QboCustomer", qbo_id=qbo_customer.id, target="Project",
        )

        existing = self.project_service.read_by_name(name) if name else None
        if existing is None:
            logger.info(f"Creating new Project from QboCustomer {qbo_customer.id}: name={name}")
            return self.project_service.create(
                name=name, description=description, status=status, customer_id=customer_id,
            )

        # The name-matched row must be re-checked for an existing, DIFFERENT
        # (QboId, RealmId) before being returned as the candidate -- the
        # dbo-only equivalent of the old mapping-table duplicate check.
        # `_stamp_project_identity`'s SetProjectQboIdentity theft-clear only
        # protects the INCOMING (qbo_id, realm_id) pair's uniqueness, not this
        # row's PRIOR identity -- it would not stop a silent re-point here.
        # Shared with `_stamp_project_identity`'s own pre-stamp re-read via
        # `_check_no_conflicting_project_identity`, so the two guards can't
        # drift out of sync with each other. Mirrors
        # `_resolve_customer_candidate`'s (U-310) Decision-2-style guard.
        self._check_no_conflicting_project_identity(existing, qbo_customer)

        logger.info(
            f"Binding existing local Project {existing.id} ({name}) to QboCustomer "
            f"{qbo_customer.id} by name match"
        )
        # customer_id write + address sync deliberately deferred to
        # _stamp_project_identity, which applies them atomically with the
        # identity stamp under the candidate's own lock (mirrors
        # _resolve_customer_candidate's U-310 precedent). name/description/
        # status are deliberately NEVER written on this branch -- U-303's
        # pre-existing rule for adopting a possibly hand-authored local
        # Project by name match: only CustomerId gets bound, every other
        # field of the pre-existing row is preserved untouched. (For the
        # sibling CREATE branch above, name/description/status are already
        # correct from `.create()`'s own arguments -- there is nothing to
        # preserve since the row didn't exist a moment ago.)
        return existing

    def _stamp_project_identity(
        self, candidate: Project, qbo_customer: QboCustomer, *, customer_id: Optional[int],
        external_customer=None,
    ) -> Optional[Project]:
        """
        `stamp_identity` for the dbo-only fast path's MISS branch (U-311),
        delegating the row-scoped lock + theft-guard + write sequence to the
        shared `stamp_dbo_identity_with_lock` (U-328/U-331 —
        `docs/design/stamp-lock-helper.md`) — see that function's own
        docstring for why a SECOND lock, keyed on the CANDIDATE's project_id,
        is needed here: `resolve_candidate` binds by NAME (a side-channel
        business key), so two different QboCustomers (different qbo_ids — no
        contention on the qbo_id-keyed lock upstream) could name-match onto
        the SAME local Project concurrently.

        `apply_fields` writes ONLY CustomerId, and only when `customer_id is
        not None` — U-303's deliberate adopt-by-name contract (see
        `_resolve_project_candidate`'s own comment) requires name/
        description/status to survive a name-match adopt untouched, so this
        MISS branch deliberately does NOT route through the shared
        `_apply_project_fields_and_sync` the fast path's HIT branch uses.
        When `customer_id is None` the closure returns `current` unchanged
        (still non-None, so the shared helper's None-guard treats it as a
        no-op, not a race) — preserving the original's "skip the write
        entirely" shape rather than turning it into a no-op UPDATE.
        `write_identity` stamps the identity AND syncs addresses in one
        same-lock step (every live copy already treats those as one atomic
        post-guard step). `on_conflict` keeps only the reconciliation-
        recording half of the former `_check_no_conflicting_project_identity`
        call — the raise itself now lives in the shared helper.
        """
        def _apply_customer_id_only(c: Project) -> Optional[Project]:
            if customer_id is None:
                return c
            c.customer_id = customer_id
            return self.project_service.repo.update_by_id(c)

        def _write_identity(c: Project) -> None:
            self.project_service.repo.set_qbo_identity(
                id=c.id, qbo_id=qbo_customer.qbo_id, realm_id=qbo_customer.realm_id,
            )
            self._sync_addresses(qbo_customer, c.id, external_customer=external_customer)

        candidate_id = coerce_id(candidate.id)
        return stamp_dbo_identity_with_lock(
            candidate_id=candidate_id,
            entity_label="Project",
            qbo_id=qbo_customer.qbo_id,
            realm_id=qbo_customer.realm_id,
            read_by_id=self.project_service.read_by_id,
            apply_fields=_apply_customer_id_only,
            write_identity=_write_identity,
            on_conflict=lambda c: self._record_project_identity_conflict_issue(
                qbo_customer=qbo_customer, local_project=c, existing_qbo_id=c.qbo_id,
            ),
        )

    @staticmethod
    def _conflicting_project_identity(local_project: Project, qbo_customer: QboCustomer) -> Optional[str]:
        """
        Pure predicate shared by `_check_no_conflicting_project_identity`
        (raises) and `heal_missing_mapping` (returns None gracefully) -- U-311
        /simplify (reuse): both used to hand-copy this exact comparison,
        which is precisely the "two-hand-kept-in-sync-copies" class
        `_check_no_conflicting_project_identity` already existed to avoid; now
        there is exactly one.

        Returns `local_project`'s existing QboId when it conflicts with
        `qbo_customer`'s (a DIFFERENT QboId, or the SAME QboId under a
        DIFFERENT RealmId -- QBO ids are only unique WITHIN a realm), else
        None (no identity yet, or a benign re-resolve to the exact same pair).
        """
        existing_qbo_id = getattr(local_project, "qbo_id", None)
        if not existing_qbo_id or (
            existing_qbo_id == qbo_customer.qbo_id
            and (getattr(local_project, "realm_id", None) or "") == (qbo_customer.realm_id or "")
        ):
            return None
        return existing_qbo_id

    def _check_no_conflicting_project_identity(
        self, local_project: Project, qbo_customer: QboCustomer,
    ) -> None:
        """
        Shared guard for `_resolve_project_candidate`'s name-matched candidate
        and `_stamp_project_identity`'s pre-stamp re-read (U-311) -- ONE
        implementation instead of two hand-kept-in-sync copies, since
        `_stamp_project_identity`'s SetProjectQboIdentity theft-clear only
        protects the INCOMING (qbo_id, realm_id) pair's uniqueness, not
        `local_project`'s PRIOR identity; it would not stop a silent re-point
        on its own.

        No-op when `_conflicting_project_identity` returns None (no QBO
        identity yet, or a benign re-resolve). Otherwise records a
        `project_identity_conflict` reconciliation issue (reusing the
        DriftType the now-deleted mapping-table-era
        `_record_identity_mapping_conflict_issue` used to emit) and raises.
        Mirrors `_check_no_conflicting_identity` (U-310).
        """
        existing_qbo_id = self._conflicting_project_identity(local_project, qbo_customer)
        if existing_qbo_id is None:
            return
        self._record_project_identity_conflict_issue(
            qbo_customer=qbo_customer, local_project=local_project, existing_qbo_id=existing_qbo_id,
        )
        raise ValueError(
            f"Project {local_project.id} already carries a DIFFERENT identity "
            f"(QboId={existing_qbo_id}, RealmId={getattr(local_project, 'realm_id', None)}) than "
            f"incoming QboCustomer {qbo_customer.qbo_id} (realm_id={qbo_customer.realm_id}) — "
            f"refusing to overwrite it."
        )

    def _record_project_identity_conflict_issue(
        self, *, qbo_customer: QboCustomer, local_project: Project, existing_qbo_id: str,
    ) -> None:
        """
        Name-match duplicate (U-311). Mirrors CustomerCustomerConnector (U-310). Reuses
        `project_identity_conflict` (previously emitted by the deleted mapping-table
        `_record_identity_mapping_conflict_issue`).
        """
        existing_realm_id = getattr(local_project, "realm_id", None)
        conflict_desc = build_duplicate_qbo_identity_conflict_desc(
            existing_qbo_id=existing_qbo_id,
            incoming_qbo_id=qbo_customer.qbo_id,
            existing_realm_id=existing_realm_id,
            incoming_realm_id=qbo_customer.realm_id,
        )
        details = (
            f"Duplicate QBO sub-customer detected. QboCustomer {qbo_customer.id} "
            f"(DisplayName='{qbo_customer.display_name}') name-matches local Project "
            f"{local_project.id} which already carries {conflict_desc}. "
            f"Resolve by merging or renaming one of the QBO sub-customers."
        )
        record_duplicate_identity_conflict(
            self.reconciliation_repo,
            drift_type="project_identity_conflict",
            entity_type="Project",
            entity_public_id=str(local_project.public_id) if local_project.public_id else None,
            qbo_id=str(qbo_customer.qbo_id) if qbo_customer.qbo_id else None,
            realm_id=qbo_customer.realm_id or "",
            details=details,
        )

    # The ONLY QBO parent-customer-ref -> dbo.Customer resolver (U-297) — the
    # four `_get_project_public_id` resolvers (bill_line_item / purchase's
    # expense_line_item / vendorcredit's bill_credit_line_item / invoice) hop
    # through qbo.CustomerProject instead, and the five vendor-ref resolvers
    # (U-284v) through qbo.VendorVendor. Same dbo-first / verify shape as all
    # nine, but returns a LOCAL INT rather than a public_id — like
    # ExpenseCodingItemService._resolve_vendor_id — because its result is
    # WRITTEN to dbo.Project.CustomerId by _apply_project_fields_and_sync, not
    # just used as a lookup key.
    # Hand-copied deliberately, mirroring _get_project_public_id's own precedent;
    # see TODO.md's U-005[reuse] entry before adding a copy or consolidating.
    # U-310: this resolver is now fully dbo-only — it reads NEITHER
    # qbo.CustomerCustomer nor qbo.Customer (the verify step is Option A's
    # `verify_identity_dbo_only`, and the legacy two-hop fallback is deleted).
    # The sibling nine still carry their own hops; those are U-311/U-312's.
    def _get_parent_customer_id(
        self, parent_ref_value: Optional[str], realm_id: Optional[str] = None
    ) -> Optional[int]:
        """
        Resolve a QBO job/sub-customer's ParentRef to a local dbo.Customer.Id,
        memoized per (realm_id, parent_ref_value) for this connector instance's
        lifetime — one connector serves a whole pull run and sub-units of one
        property share a parent. See _resolve_parent_customer_id for the
        resolution itself.
        """
        if not parent_ref_value:
            return None

        cache_key = (realm_id, parent_ref_value)
        if cache_key in self._parent_customer_cache:
            return self._parent_customer_cache[cache_key]

        result = self._resolve_parent_customer_id(parent_ref_value, realm_id)
        self._parent_customer_cache[cache_key] = result
        return result

    def _resolve_parent_customer_id(
        self, parent_ref_value: str, realm_id: Optional[str] = None
    ) -> Optional[int]:
        """
        Uncached resolution — see _get_parent_customer_id, which caches this.

        Args:
            parent_ref_value: the parent's QBO Customer id (qbo.Customer.ParentRefValue)
            realm_id: the CHILD's realm — the only one in hand here, and the same
                realm as its parent by construction (a QBO sub-customer cannot
                live in a different company file than its parent).

        Returns:
            int: local dbo.Customer.Id, or None.
        """
        # U-297: try dbo.Customer's native QboId/RealmId directly (U-238c
        # stamped every row). U-310 (Option A, `docs/design/wave5.md` §2):
        # the verify step is now `verify_identity_dbo_only` — a plain re-read
        # of dbo.Customer by the resolved row's OWN (qbo_id, realm_id),
        # trusted only when it still resolves back to the same local id — and
        # reads NO `qbo.*` mapping table at all.
        #
        # There is no legacy two-hop fallback left: the old
        # `qbo.Customer` -> `qbo.CustomerCustomer` hop was this resolver's
        # only other data source, and Wave 5 retires that mapping table. So a
        # miss or a refused verify now returns None outright. Per §2's
        # "consequence worth flagging": this resolver used to be ADVISORY (a
        # verify disagreement degraded gracefully to the slower legacy hop);
        # once the mapping table's data source is gone it becomes
        # hard-stop-equivalent BY CONSTRUCTION, not by choice. Measured as a
        # no-op today (0 dbo<->mapping disagreements live, §1), but a future
        # disagreement that used to degrade now resolves the parent to None —
        # the Project simply syncs without a CustomerId rather than binding to
        # an unverified parent, which is the safe side of that trade.
        direct_customer = self.customer_service.read_by_qbo_identity(parent_ref_value, realm_id)
        if direct_customer:
            verified_qbo_id = verify_identity_dbo_only(
                direct_customer,
                read_direct_by_qbo_identity=self.customer_service.read_by_qbo_identity,
            )
            if verified_qbo_id:
                logger.debug(f"Found Customer {direct_customer.id} via direct dbo QboId lookup")
                return direct_customer.id
        return None

    def _apply_project_fields_and_sync(
        self,
        project: Project,
        *,
        qbo_customer: QboCustomer,
        name: str,
        description: str,
        status: str,
        customer_id: Optional[int],
        external_customer=None,
    ) -> Optional[Project]:
        """
        Write the QboCustomer-derived fields onto an existing Project, persist it,
        and sync its addresses. This is the `apply_fields` callback for the
        dbo-identity fast path (`sync_from_qbo_customer` above).

        Returns `None` on a ROWVERSION-race/concurrent-delete `update_by_id`
        miss instead of raising directly (U-316) — `run_identity_fastpath_
        dbo_only`'s own `_apply()` now raises `raise_concurrent_write_race`
        unconditionally whenever `apply_fields` returns `None`, so this
        method staying silent on a miss (rather than raising twice) is what
        keeps that single raise as the ONE place the guarantee lives.
        """
        project.name = preserve_human_edited_name(project.name, name)
        project.description = description
        project.status = status
        project.customer_id = customer_id
        updated = self.project_service.repo.update_by_id(project)
        if updated is None:
            return None
        self._sync_addresses(qbo_customer, updated.id, external_customer=external_customer)
        return updated

    def _sync_addresses(
        self, qbo_customer: QboCustomer, project_id: int, *, external_customer=None,
    ) -> None:
        """
        Sync billing and shipping addresses from QboCustomer to ProjectAddress/Address.

        U-506 P1 — the WRITER half of the blank Draw-Request-"To:"-block fix
        (P0 hardened the READER in `entities/invoice/api/router.py`). Two
        changes over the pre-U-506 shape, which keyed purely on ID PRESENCE:

        1. BLANKNESS, not presence. QBO hands back placeholder address rows —
           an `Id` with empty Line1/City/PostalCode. 138 job customers
           reference 32 staging addresses of which only 3 carry any content;
           the other 29 passed truthiness, were minted into dbo.Address as
           blank rows and linked (191 of 799 dbo.Address rows are blank as a
           result). A blank staging row is now treated as absent.

        2. PARENT FALLBACK on the BILLING slot, first NON-BLANK wins:
               own bill -> parent bill
           ⚠️ U-506 P2 narrowed this from `own bill -> own ship -> parent bill
           -> parent ship`. Both ship links were job-SITE addresses with no
           business in an owner-mailing slot; own-ship additionally OUTRANKED
           the parent's real remit-to.

           RE-MEASURED against live data 2026-09-23, and the narrowing costs
           NOTHING — the figures below are unchanged, not merely close:
               pre-P1  (own bill -> own ship)       2/138 projects,  64 invoices
               post-P1 (4-link)                    63/138 projects, 706 invoices
               post-P2 (own bill -> parent bill)   63/138 projects, 706 invoices
           Winner breakdown under the 4-link chain: parent bill 61, own bill 2,
           NEITHER SHIP LINK EVER WON. `parent ship` is non-blank on 58 parents
           but always sits behind `parent bill`; `own ship` outranks nothing
           today. So P2 removed a latent wrong-address path with ZERO live
           instances and 58 latent ones — a parent whose BillAddr ever goes
           blank would have started supplying a SIBLING project's street.
           The residual 75 name-only projects are irreducible: those parents'
           BillAddrId points at a blank staging row too; QBO holds nothing more.

           ⚠️ SEPARATE, STILL OPEN — the numbers above are what a PULL would
           produce, not what is linked. `dbo.ProjectAddress` today holds a
           BILLING link for only 5 projects / 105 invoices. The connector writes
           on pull and nothing backfills, so 58 projects' worth of coverage does
           not materialise on deploy. That is the outstanding U-506 backfill. See PROJECT_BILLING_ADDRESS_IS_OWNER_MAILING for
           WHY the parent's mailing address is the right thing to inherit, and
           for the one-line flip if that reading is ever rejected.

           ⚠️ U-513 repointed the PARENT link off `qbo.PhysicalAddress`: it now
           reads `dbo.Address` directly by the parent's synthetic
           `<parent QboId>_bill` identity (`_parent_billing_address_id`), and
           `CustomerCustomerConnector` — not this fallback — is what MINTS that
           row. See `_parent_billing_address_id` for why the old shape was
           circular.

           ⚠️ U-513 ph2.5 moved the job's OWN link (Link 1) too: with the
           external payload threaded it projects from the INLINE `BillAddr`
           (`_own_billing_address_id` -> `_address_id_from_inline`), and only
           falls back to the staging read when no payload reached this
           connector. Identity strings are unchanged throughout, so no
           dbo.Address was re-keyed by either move.

        The SHIPPING slot deliberately does NOT inherit: under property
        semantics a parent's address is a SIBLING project's street, wrong for
        16 of the 61 newly-covered projects. Shipping stays job-only and is the
        only place a future property-address feature belongs. ph2.5 gave it the
        SAME payload/staging seam as billing Link 1 and gave it NO chain — it
        resolves `_own_shipping_address_id` and nothing else, in either branch.

        When NOTHING resolves we do NOTHING — no blank dbo.Address is minted
        and no ProjectAddress link is written or repointed. Each slot stays
        failure-isolated: an address problem must not fail the customer pull —
        which is why the payload is re-validated INSIDE each slot's own `try`
        rather than once above them (`_payload_for`).

        Args:
            qbo_customer: QboCustomer with bill_addr_id / ship_addr_id / parent_ref_value
            project_id: Database ID of the Project
            external_customer: the ORIGINAL QBO Customer payload for this same
                job, or None. Present -> both own-address slots project from its
                inline `BillAddr`/`ShipAddr`; absent -> both read staging.
        """
        # BILLING: the fallback chain.
        try:
            address_id = self._resolve_billing_address_id(
                qbo_customer, self._payload_for(qbo_customer, external_customer)
            )
            if address_id:
                self._ensure_project_address(project_id, address_id, ADDRESS_TYPE_BILLING)
                logger.debug(f"Synced billing address {address_id} for Project {project_id}")
            else:
                # U-506 P2 — do NOT simply leave the slot: a stale CONNECTOR-MINTED
                # link here mails a financial document to the WRONG PARTY.
                #
                # `_apply_project_fields_and_sync` repoints `project.customer_id`
                # unconditionally, so when a job is re-parented in QBO (or merged)
                # onto an owner whose own address slots are blank, the chain
                # resolves None while the BILLING link still points at the FORMER
                # owner's dbo.Address. The Draw Request then renders the NEW
                # owner's name beside the OLD owner's street, indefinitely --
                # nothing else ever clears it.
                #
                # This is specific to inheritance: before the parent fallback the
                # slot could only ever hold the job's OWN address, so a stale link
                # was at worst the same party's out-of-date address. Once a
                # PARENT's address can occupy the slot, "stale" and "someone
                # else's" become the same state.
                self._clear_stale_connector_billing_link(project_id)
        except Exception as e:
            logger.error(f"Failed to sync billing address for Project {project_id}: {e}")

        # SHIPPING: job-only, blank-guarded. NEVER inherits from the parent.
        #
        # ⚠️ ONE candidate, by design — there is no `_resolve_shipping_address_id`
        # and no candidate generator to add a second link to. Billing has a chain
        # because the OWNER'S mailing address legitimately lives on the parent;
        # a job's ShipAddr is the SITE, and a parent's address is a SIBLING
        # project's street (wrong for 16 of 61 projects — see
        # PROJECT_BILLING_ADDRESS_IS_OWNER_MAILING). Moving this slot onto the
        # payload does not change that: the payload's `ship_addr` belongs to
        # THIS job and there is no parent payload in scope to fall back to.
        try:
            address_id = self._own_shipping_address_id(
                qbo_customer, self._payload_for(qbo_customer, external_customer)
            )
            if address_id:
                self._ensure_project_address(project_id, address_id, ADDRESS_TYPE_SHIPPING)
                logger.debug(f"Synced shipping address {address_id} for Project {project_id}")
        except Exception as e:
            logger.error(f"Failed to sync shipping address for Project {project_id}: {e}")

    def _payload_for(self, qbo_customer: QboCustomer, external_customer):
        """
        The external payload to project THIS job's own addresses from, or None
        when the caller threaded none — or when the one it threaded belongs to a
        DIFFERENT customer (U-513 ph2.5, mirroring
        `VendorVendorConnector._bill_address_from_payload`'s refusal).

        The cross-wiring guard is defense in depth, not a live condition:
        `QboCustomerService._sync_to_projects` keys the map by
        `external_by_id.get(row.qbo_id)`, the exact value `_upsert_customer`
        staged as `qbo_id`, so a mis-pair cannot arise from the pull. But taking
        the ADDRESS off one customer's payload while writing it under ANOTHER
        customer's synthetic identity is silent cross-wiring — a project would
        render a stranger's street on a payment request with nothing raised — so
        a mismatch refuses the payload entirely and both slots degrade to their
        staging reads.

        Called once per SLOT rather than once per call so that each slot stays
        inside its own failure-isolated `try`: a malformed payload must cost at
        most one address, never the Project projection (which would hold the
        pull watermark over an address — see `_sync_addresses`).
        """
        if external_customer is None:
            return None
        external_id = str(external_customer.id) if external_customer.id else None
        staging_qbo_id = qbo_customer.qbo_id or None
        if not external_id or external_id != staging_qbo_id:
            logger.error(
                "Customer payload/staging mismatch for QboCustomer %s: staging QboId=%s, "
                "payload Id=%s. Ignoring the inline BillAddr/ShipAddr and falling back "
                "to staging.",
                qbo_customer.id, staging_qbo_id, external_id,
            )
            return None
        return external_customer

    def _resolve_billing_address_id(
        self, qbo_customer: QboCustomer, external_customer=None,
    ) -> Optional[int]:
        """
        The `dbo.Address` id of the first NON-BLANK link in the billing chain,
        or None when the whole chain is blank/absent (U-506 P1). None means DO
        NOTHING — never mint a blank dbo.Address to fill the slot.

        ⚠️ U-513 changed the UNIT this returns: it used to be a
        `qbo.PhysicalAddress` STAGING id that the caller then pushed through
        `sync_from_qbo_to_address`. It is now a `dbo.Address` id, already
        resolved — because the links no longer share a source. As of ph2.5 the
        job's own BillAddr comes from the INLINE payload (or, with no payload,
        from staging), while the PARENT's comes straight out of `dbo.Address`
        with no staging row in the picture at all. Each candidate function owns
        its own resolution, and the caller just links what it is handed.
        """
        for address_id in self._billing_address_candidates(qbo_customer, external_customer):
            if address_id:
                return address_id
        return None

    def _billing_address_candidates(
        self, qbo_customer: QboCustomer, external_customer=None,
    ) -> Iterator[Optional[int]]:
        """
        The billing chain, LAZILY: own bill -> parent bill. A generator on
        purpose — the parent is read only once the job's own BillAddr has come
        up blank/absent, so a job that carries its own address costs zero extra
        round trips.

        ⚠️ U-506 P2 — BOTH ShipAddr links were REMOVED from this chain.
        The slot means the OWNER'S MAILING address (decided 2026-09-23). A QBO
        sub-customer's ShipAddr is the JOB SITE, so neither ship link can be an
        owner mailing address:

          * own ship  — the construction site itself. It previously sat at
            link 2, AHEAD of the parent's BillAddr, so a job with a site
            address and an owner with a real remit-to rendered the SITE under
            "TO OWNER:" while the owner's mailing address sat unused in
            staging. Directly contradicts the decided semantic.
          * parent ship — a SIBLING project's street. This is the same "16
            project" error the SHIPPING slot has always been protected from;
            it was wrong here for the identical reason.

        Dropping them degrades some projects to name-only rather than printing
        a wrong address on a payment request, which is the safe direction: a
        missing address is visible, a plausible-but-wrong one is not.
        """
        yield self._own_billing_address_id(qbo_customer, external_customer)
        if not PROJECT_BILLING_ADDRESS_IS_OWNER_MAILING:
            return
        yield self._parent_billing_address_id(
            qbo_customer.parent_ref_value, qbo_customer.realm_id
        )

    def _own_billing_address_id(
        self, qbo_customer: QboCustomer, external_customer=None,
    ) -> Optional[int]:
        """
        Link 1: the JOB's own BillAddr, as a `dbo.Address` id — or None when it
        is absent or blank.

        U-513 ph2.5: projected from the INLINE `BillAddr` when the caller
        threaded the payload, and read back out of `qbo.PhysicalAddress` only
        when it did not. Same seam as
        `VendorVendorConnector._bill_address_from_payload` /
        `_bill_address_from_staging`, chosen the same way.
        """
        if external_customer is not None:
            return self._address_id_from_inline(
                qbo_customer,
                external_customer.bill_addr,
                qbo_id=billing_address_qbo_id(qbo_customer.qbo_id),
                slot="BillAddr",
            )
        return self._own_address_id_from_staging(qbo_customer.bill_addr_id)

    def _own_shipping_address_id(
        self, qbo_customer: QboCustomer, external_customer=None,
    ) -> Optional[int]:
        """
        The SHIPPING slot's ONLY candidate: the JOB's own ShipAddr, as a
        `dbo.Address` id — or None when it is absent or blank (U-513 ph2.5).

        ⚠️ There is deliberately NO fallback here, and adding one is the single
        most damaging change that could be made to this module. Shipping is
        job-only: a parent's address is a SIBLING project's street, wrong for 16
        of 61 projects, and this slot is the only place a future property-address
        feature belongs (see `_sync_addresses` and
        PROJECT_BILLING_ADDRESS_IS_OWNER_MAILING). The payload move does not
        create a new opportunity for one — `external_customer` is THIS job's
        payload and carries no parent address at all.

        Same payload/staging seam as billing Link 1, and the same blankness
        predicate through the same two helpers, so the two branches cannot
        diverge on what counts as an address.
        """
        if external_customer is not None:
            return self._address_id_from_inline(
                qbo_customer,
                external_customer.ship_addr,
                qbo_id=shipping_address_qbo_id(qbo_customer.qbo_id),
                slot="ShipAddr",
            )
        return self._own_address_id_from_staging(qbo_customer.ship_addr_id)

    def _address_id_from_inline(
        self, qbo_customer: QboCustomer, inline_address, *, qbo_id: str, slot: str,
    ) -> Optional[int]:
        """
        Project one of the job's OWN address slots straight from the inline QBO
        payload onto `dbo.Address`, and return its id — U-513's direction, and
        the only source that survives the `qbo.PhysicalAddress` sunset.

        Identity is the caller's synthetic `<qbo_id>_bill` / `<qbo_id>_ship`,
        byte-identical to what the staging hop stamped, so existing dbo.Address
        rows are MATCHED, not re-keyed. Realm comes from the staging row — the
        same realm the staging address write used — and is passed through
        unmodified so the address connector's realm scoping can fail closed.

        Returns None (mint nothing, link nothing) when QBO sent no address
        object at all, or sent a blank one. Blankness is
        `address_fields_are_blank`, the family's ONE rule — the SAME function
        `_is_blank_staged_address` applies on the staging branch, which is what
        keeps the two branches from silently disagreeing about whether a
        placeholder counts as an address.
        """
        if inline_address is None:
            return None
        if address_fields_are_blank(
            inline_address.line1, inline_address.city, inline_address.postal_code
        ):
            # Blank means ABSENT (see `address_fields_are_blank`): 191 of 799
            # dbo.Address rows are blank precisely because the pre-U-506 writer
            # keyed on presence. Never mint one, never link one.
            logger.debug(
                f"QboCustomer {qbo_customer.qbo_id} has a blank inline {slot} -- "
                f"treating it as absent (no dbo.Address minted, no ProjectAddress written)"
            )
            return None
        address = self.address_connector.sync_address_from_external(
            qbo_id=qbo_id,
            realm_id=qbo_customer.realm_id,
            line1=inline_address.line1,
            line2=inline_address.line2,
            city=inline_address.city,
            country_sub_division_code=inline_address.country_sub_division_code,
            postal_code=inline_address.postal_code,
            source_ref=f"QboCustomer:{qbo_customer.qbo_id}",
        )
        return coerce_id(address.id)

    def _own_address_id_from_staging(self, staging_id: Optional[int]) -> Optional[int]:
        """
        ⚠️ TRANSITIONAL — the pre-U-513 path for one of the job's own address
        slots: blank-check the `qbo.PhysicalAddress` row the staging FK points
        at, then let `sync_from_qbo_to_address` mint/refresh the dbo row from it.
        Shared by BOTH slots because both behaved identically before ph2.5, and
        sharing is what guarantees they still do.

        This is now the ONLY reader of `bill_addr_id` / `ship_addr_id` left in
        this package. It is reachable from exactly two places, neither of them
        the production pull:

          1. a direct `sync_from_qbo_customer(row)` call with no second argument
             (`QboCustomerService._sync_to_projects` always threads the payload
             it staged from, so the pull never lands here);
          2. `heal_missing_mapping`, which binds a name-matched Project from an
             invoice pull and genuinely has no Customer payload in scope.

        Delete it — with the FK columns and the table — in ph3b. Until then it
        is what lets (2) keep resolving addresses at all.
        """
        if not staging_id or self._staged_address_is_blank(staging_id):
            return None
        address = self.address_connector.sync_from_qbo_to_address(staging_id)
        return coerce_id(address.id)

    def _parent_billing_address_id(
        self, parent_ref_value: Optional[str], realm_id: Optional[str]
    ) -> Optional[int]:
        """
        Link 2: the PARENT customer's BillAddr, read straight out of
        `dbo.Address` by its synthetic `<parent QboId>_bill` identity — or None
        when the parent has no address, has a blank one, or does not exist.

        ⚠️ U-513 — this used to read the parent's `qbo.Customer` STAGING row for
        its `BillAddrId`. Two reasons that had to go, beyond the staging sunset
        itself:

          * The parent is usually NOT in the current delta page (138 job
            customers, 73 distinct parents, most of which QBO has not touched),
            so this read has to hit something PERSISTENT either way.
          * It was CIRCULAR. `sync_from_qbo_to_address` on the parent's staging
            id is what CREATED the very `<parent>_bill` dbo.Address rows this
            fallback depends on — the fallback was minting its own inputs. A
            brand-new parent would therefore have gotten an address only because
            one of its children happened to pull first. With staging gone that
            breaks outright, which is why `CustomerCustomerConnector._project_
            own_billing_address` now owns the mint and this side only READS.

        `ReadAddressByQboIdAndRealmId` is realm-scoped and fails closed (a row's
        RealmId must equal the one passed, NULL-to-NULL included), and
        `realm_id` here is the CHILD's — the same realm as its parent by
        construction (a QBO sub-customer cannot live in a different company file
        than its parent), exactly as `_resolve_parent_customer_id` documents.

        No `parent_ref_value` -> None WITHOUT touching the service (a top-level
        job has no parent to inherit from, and reading by a None qbo_id would be
        a lookup for nothing). A missing row -> None as well, never a raise: the
        caller simply finds no candidate and leaves the slot untouched.
        Memoized per (realm_id, parent_ref_value), MISSES included, alongside
        `_parent_customer_cache`.
        """
        if not parent_ref_value:
            return None

        cache_key = (realm_id, parent_ref_value)
        if cache_key in self._parent_billing_address_cache:
            return self._parent_billing_address_cache[cache_key]

        address = self.address_service.read_by_qbo_identity(
            billing_address_qbo_id(parent_ref_value), realm_id
        )
        address_id = None
        if address is not None and not self._is_blank_dbo_address(address):
            address_id = coerce_id(address.id)
        self._parent_billing_address_cache[cache_key] = address_id
        return address_id

    @staticmethod
    def _is_blank_dbo_address(address) -> bool:
        """
        The blankness test against a `dbo.Address` row (U-513) — the SAME rule
        as `_is_blank_staged_address`, applied to the dbo column names
        (`street_one` / `city` / `zip`).

        Load-bearing, not belt-and-braces: 191 of the 799 existing dbo.Address
        rows are completely blank, minted by the pre-U-506 connector that keyed
        on id presence. Linking one of those to a project's BILLING slot would
        re-introduce exactly the name-only "To:" block U-506 P1 fixed, and would
        also mask the `_clear_stale_connector_billing_link` path behind a link
        that merely LOOKS resolved.
        """
        return address_fields_are_blank(address.street_one, address.city, address.zip)

    def _staged_address_is_blank(self, qbo_physical_address_id: int) -> bool:
        """
        True when the `qbo.PhysicalAddress` staging row behind this id carries
        no content — QBO's placeholder shape: an Id with empty Line1 / City /
        PostalCode (U-506 P1). A row that cannot be read AT ALL counts as blank
        too: `sync_from_qbo_to_address` would raise on it anyway, and the chain
        should just move on.

        Reads through `self.address_connector`'s own staging service rather
        than taking a SECOND injected handle — that connector is the thing that
        reads (and syncs) these exact rows, so a separate handle would be a
        second source of truth for one table.
        """
        staged = self.address_connector.qbo_physical_address_service.read_by_id(
            qbo_physical_address_id
        )
        return self._is_blank_staged_address(staged)

    @staticmethod
    def _is_blank_staged_address(staged) -> bool:
        """
        The blankness test against a `qbo.PhysicalAddress` staging row,
        independent of the read (U-506 P1). Pure, so it can be exercised without
        a staging repo. Fields are read directly, not via getattr — a renamed
        QboPhysicalAddress field must break loudly rather than silently make
        every address look blank.

        A row that cannot be read AT ALL (None) counts as blank.

        The RULE itself lives in `address_fields_are_blank` (U-513) so this and
        `_is_blank_dbo_address` — and the parent connector's own mint guard —
        cannot drift apart. Three hand-kept copies of "line1 + city + postal,
        stripped" is exactly how one of them would quietly stop matching.
        """
        if staged is None:
            return True
        return address_fields_are_blank(staged.line1, staged.city, staged.postal_code)

    def _clear_stale_connector_billing_link(self, project_id: int) -> None:
        """Drop a BILLING link the connector minted, when the chain now resolves
        nothing. Leaves HAND-SET links alone.

        `dbo.Address.qbo_id` is the discriminator: the identity fast path stamps
        it on every address this connector mints, and a human-entered address has
        none. So:

          * qbo_id IS NOT NULL -> the connector put it there, and the connector's
            current chain has no address at all, so it cannot still be current.
            Remove it. The next pull re-creates the link the moment QBO has an
            address again.
          * qbo_id IS NULL -> hand-set. NEVER touched. This is the half of
            `test_blank_chain_leaves_an_existing_link_untouched` that records a
            real defect (the connector must not clobber human data), and it is
            preserved exactly.

        Failing to name-only is deliberately the safe direction: an absent
        address is visible to whoever sends the packet, a plausible-but-wrong one
        is not. It also closes the narrower case of an address the owner DELETED
        in QBO, which used to keep rendering forever.
        """
        for pa in self.project_address_service.read_by_project_id(project_id) or []:
            if getattr(pa, "address_type_id", None) != ADDRESS_TYPE_BILLING:
                continue
            address_id = getattr(pa, "address_id", None)
            if not address_id:
                continue
            address = self.address_service.read_by_id(address_id)
            if address is None or not getattr(address, "qbo_id", None):
                # Hand-set (or unreadable) -- not ours to clear.
                logger.debug(
                    f"Project {project_id} billing link {pa.id} is hand-set "
                    f"(address {address_id} has no qbo_id) — left untouched"
                )
                continue
            self.project_address_service.repo.delete_by_id(pa.id)
            logger.info(
                f"draw_request.stale_billing_link_cleared project_id={project_id} "
                f"project_address_id={pa.id} address_id={address_id} "
                f"qbo_id={address.qbo_id!r} — the billing chain resolved nothing, so a "
                f"connector-minted link could only be a FORMER owner's address"
            )

    def _ensure_project_address(self, project_id: int, address_id: int, address_type_id: int) -> None:
        """
        Ensure a ProjectAddress record exists linking Project to Address.
        Creates if not exists, updates if exists with different address.
        
        Args:
            project_id: Database ID of the Project
            address_id: Database ID of the Address
            address_type_id: Type of address (billing/shipping)
        """
        # Check for existing ProjectAddress by project_id and address_type
        existing_addresses = self.project_address_service.read_by_project_id(project_id)
        existing = None
        for pa in existing_addresses:
            if pa.address_type_id == address_type_id:
                existing = pa
                break
        
        if existing:
            if existing.address_id != address_id:
                # Update with new address
                existing.address_id = address_id
                self.project_address_service.repo.update_by_id(existing)
                logger.debug(f"Updated ProjectAddress {existing.id} with new address {address_id}")
        else:
            # Create new ProjectAddress
            self.project_address_service.create(
                project_id=project_id,
                address_id=address_id,
                address_type_id=address_type_id
            )
            logger.debug(f"Created ProjectAddress for Project {project_id}, Address {address_id}, Type {address_type_id}")

    def create_mapping(
        self,
        project_id: int,
        qbo_customer_id: int,
        *,
        qbo_id: Optional[str],
        realm_id: Optional[str],
    ) -> None:
        """
        Bind a Project to its QBO identity by stamping dbo.Project.QboId/RealmId.

        U-314-prereq: dbo.Project.QboId/RealmId is the SOLE identity store — this
        no longer reads or writes a qbo.CustomerProject mapping row (that table is
        being retired; U-314 drops it). `qbo_customer_id` stays in the signature
        for the caller's symmetry but is no longer persisted.

        The sole caller is `heal_missing_mapping`, which has already (a) confirmed
        via `_conflicting_project_identity` that this Project does not carry a
        DIFFERENT qbo identity, and (b) been reached only on a genuine dbo-miss
        (`InvoiceInvoiceConnector._get_project_public_id` does a
        `read_by_qbo_identity` first and only falls through to heal on a miss), so
        no OTHER Project holds `qbo_id` and `SetProjectQboIdentity`'s theft-clear
        has nothing to steal. The former mapping-table 1:1 validations are thus
        redundant and were removed with the mapping write.
        """
        self.project_service.repo.set_qbo_identity(
            id=project_id,
            qbo_id=qbo_id,
            realm_id=realm_id,
        )

    def heal_missing_mapping(self, qbo_customer) -> Optional[Project]:
        '''
        Auto-heal a MISSING CustomerProject mapping for a QboCustomer by binding an
        existing local Project matched EXACTLY by name. NEVER creates a new Project.

        Returns the bound Project, or None when no local Project can be resolved
        (callers must fail loud rather than mint). Shared by the invoice-pull
        connector to close the no-invoice window on a (possibly transient) missing
        mapping without duplicating the bind recipe.
        '''
        # Only job/sub-customers map to Projects (parity with sync_from_qbo_customer's
        # is_job gate at the top of this class). A non-job (top-level) customer must NOT be
        # name-bound to a Project — return None so the invoice caller fails loud instead of
        # wrong-binding an invoice onto an unrelated Project that merely shares a name.
        if not qbo_customer.is_job:
            return None
        project_name = qbo_customer.display_name or qbo_customer.company_name or ''
        if not project_name:
            return None
        existing_local = self.project_service.read_by_name(project_name)
        if not existing_local:
            return None
        # U-311 fix (Codex xhigh round-2 P1, corrected round-3 -- ReadProjectByName
        # does NOT project QboId/RealmId at all (entities/project/sql/dbo.project.sql),
        # so checking existing_local.qbo_id straight off the read_by_name result is
        # dead against real data -- it would only ever be None/absent, exactly the
        # same class of gap U-310's own Codex round-1 P2 found for
        # CustomerCustomerConnector's ReadCustomerByName. Re-read via read_by_id,
        # which DOES project QboId/RealmId, mirroring _stamp_project_identity's own
        # "this is the read that actually protects production" re-read below.
        #
        # dbo-only pulls (this connector's own sync_from_qbo_customer, above) no
        # longer create a qbo.CustomerProject mapping row at all, so the
        # mapping-table check further down can no longer be trusted as a proxy for
        # "this Project already carries a DIFFERENT identity" -- a Project synced
        # via the new dbo-only path has NO mapping row regardless of whether its
        # dbo QboId already differs from this QboCustomer's. Without this guard, a
        # genuine QBO-side rename/duplicate that name-matches an already-identified
        # Project would silently steal its identity via create_mapping's
        # set_qbo_identity call below. Graceful record+None (not a raise), matching
        # this method's existing mapping-table-based duplicate branch immediately
        # below and this method's own "never creates, callers fail loud on None"
        # documented contract.
        existing_local_with_identity = self.project_service.read_by_id(existing_local.id) or existing_local
        existing_qbo_id = self._conflicting_project_identity(existing_local_with_identity, qbo_customer)
        if existing_qbo_id is not None:
            self._record_project_identity_conflict_issue(
                qbo_customer=qbo_customer, local_project=existing_local_with_identity, existing_qbo_id=existing_qbo_id,
            )
            return None
        # U-314-prereq: the legacy qbo.CustomerProject duplicate check is retired —
        # dbo.Project.QboId is the sole identity store. `_conflicting_project_identity`
        # above already refuses to rebind existing_local if IT carries a DIFFERENT
        # identity. This dbo-native guard replaces the removed mapping-table
        # "qbo_customer already mapped to another Project" check: refuse to bind if a
        # DIFFERENT Project already holds this (qbo_id, realm). heal normally reaches
        # here only on a dbo-miss for the INVOICE's realm, but the identity is stamped
        # under the QboCustomer's OWN realm — which can differ if the invoice realm was
        # falsy — and SetProjectQboIdentity's theft-clear would then silently STEAL the
        # identity from that other Project. Re-check under the realm actually stamped.
        existing_holder = self.project_service.read_by_qbo_identity(
            qbo_customer.qbo_id, qbo_customer.realm_id
        )
        if existing_holder is not None and coerce_id(existing_holder.id) != coerce_id(existing_local.id):
            record_mapping_issue(
                self.reconciliation_repo,
                drift_type="duplicate_qbo_customer",
                entity_type="Project",
                entity_public_id=str(existing_local.public_id) if existing_local.public_id else None,
                qbo_id=str(qbo_customer.qbo_id) if qbo_customer.qbo_id else None,
                realm_id=qbo_customer.realm_id or "",
                details=(
                    f"Refusing to bind QboCustomer {qbo_customer.id} "
                    f"(QboId={qbo_customer.qbo_id}, DisplayName='{qbo_customer.display_name}') "
                    f"to name-matched Project {existing_local.id}: Project {existing_holder.id} "
                    f"already holds that dbo identity (realm {qbo_customer.realm_id!r}). Binding "
                    f"would steal it via the identity theft-clear. Resolve the QBO sub-customer "
                    f"name collision upstream."
                ),
            )
            return None
        # Bind by stamping dbo identity directly (no qbo.CustomerProject row).
        self.create_mapping(
            project_id=existing_local.id,
            qbo_customer_id=qbo_customer.id,
            qbo_id=qbo_customer.qbo_id,
            realm_id=qbo_customer.realm_id,
        )
        self._sync_addresses(qbo_customer, existing_local.id)
        logger.info(
            f'Auto-healed missing CustomerProject mapping: bound Project {existing_local.id} '
            f'({project_name}) to QboCustomer {qbo_customer.id} by name match'
        )
        return existing_local
