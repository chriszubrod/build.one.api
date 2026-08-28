# Python Standard Library Imports
import logging
from typing import Optional

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
from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository
from integrations.intuit.qbo.physical_address.connector.business.service import PhysicalAddressAddressConnector
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from integrations.intuit.qbo.base.reconciliation_recorder import record_mapping_issue
from entities.customer.business.service import CustomerService
from entities.project.business.service import ProjectService
from entities.project.business.model import Project
from entities.project_address.business.service import ProjectAddressService

logger = logging.getLogger(__name__)

# Address type IDs (these would typically come from a lookup table)
ADDRESS_TYPE_BILLING = 1
ADDRESS_TYPE_SHIPPING = 2


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
    ):
        """Initialize the CustomerProjectConnector."""
        self.project_service = project_service or ProjectService()
        self.project_address_service = project_address_service or ProjectAddressService()
        self.address_connector = address_connector or PhysicalAddressAddressConnector()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()
        # U-297: previously fed _resolve_parent_customer_id; U-310 repointed
        # that resolver onto Option A (dbo-only verify), so qbo_customer_repo
        # is DEAD there too (U-311 also repointed this connector's OWN pull
        # onto Option B, so it's not used anywhere in this class any more).
        # Its own class (QboCustomerRepository, the qbo.Customer staging repo)
        # is untouched by U-314's drop and stays a live Pass-2/simplify
        # candidate for a future dead-DI-param pass. (The sibling
        # customer_mapping_repo param -- CustomerCustomerRepository, dropped
        # by U-314 -- was removed outright rather than neutered: unlike
        # vendor_vendor_repo/customer_project_repo on other connectors, its
        # only 5 test call sites are all in this unit's own touched files.)
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

    def sync_from_qbo_customer(self, qbo_customer: QboCustomer) -> Project:
        """
        Sync data from QboCustomer to Project module, via the dbo-only identity
        fast path (U-311 — Wave-5 Option B, mirrors U-310's
        `CustomerCustomerConnector` / U-313's `VendorVendorConnector`).

        Args:
            qbo_customer: QboCustomer record (must be a job/sub-customer with Job=true)

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
            ),
        )
        if outcome.entity is None:
            # U-316: no longer race-reachable (see run_identity_fastpath_
            # dbo_only's Raises docstring) — kept as a backstop for a falsy
            # qbo_customer.qbo_id, which nothing upstream guards against yet
            # (TODO.md follow-up).
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
            self._sync_addresses(qbo_customer, c.id)

        candidate_id = coerce_id(candidate.id)
        return stamp_dbo_identity_with_lock(
            candidate_id=candidate_id,
            entity_label="Project",
            qbo_id=qbo_customer.qbo_id,
            realm_id=qbo_customer.realm_id,
            read_by_id=self.project_service.read_by_id,
            apply_fields=_apply_customer_id_only,
            write_identity=_write_identity,
            on_conflict=lambda c: self._raise_project_identity_conflict_issue(
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
        `_raise_identity_mapping_conflict_issue` used to emit) and raises.
        Mirrors `_check_no_conflicting_identity` (U-310).
        """
        existing_qbo_id = self._conflicting_project_identity(local_project, qbo_customer)
        if existing_qbo_id is None:
            return
        self._raise_project_identity_conflict_issue(
            qbo_customer=qbo_customer, local_project=local_project, existing_qbo_id=existing_qbo_id,
        )
        raise ValueError(
            f"Project {local_project.id} already carries a DIFFERENT identity "
            f"(QboId={existing_qbo_id}, RealmId={getattr(local_project, 'realm_id', None)}) than "
            f"incoming QboCustomer {qbo_customer.qbo_id} (realm_id={qbo_customer.realm_id}) — "
            f"refusing to overwrite it."
        )

    def _raise_project_identity_conflict_issue(
        self, *, qbo_customer: QboCustomer, local_project: Project, existing_qbo_id: str,
    ) -> None:
        """
        Record a name-match-vs-different-existing-identity duplicate (U-311).
        Mirrors `CustomerCustomerConnector._raise_duplicate_qbo_customer_issue`
        (U-310) exactly. (`heal_missing_mapping`'s own duplicate check used to
        be a separate mapping-table-based `_raise_duplicate_qbo_customer_issue`
        on this class -- U-314-prereq repointed it onto the dbo-native
        `duplicate_qbo_customer` recording inline in `heal_missing_mapping`
        below, and U-314 deleted the now-dead mapping-table version.)
        """
        existing_realm_id = getattr(local_project, "realm_id", None)
        if existing_qbo_id == qbo_customer.qbo_id:
            conflict_desc = (
                f"the SAME QboId {existing_qbo_id} but a DIFFERENT RealmId "
                f"({existing_realm_id!r} vs incoming {qbo_customer.realm_id!r})"
            )
        else:
            conflict_desc = f"a DIFFERENT QboId {existing_qbo_id} (realm {existing_realm_id!r})"
        details = (
            f"Duplicate QBO sub-customer detected. QboCustomer {qbo_customer.id} "
            f"(DisplayName='{qbo_customer.display_name}') name-matches local Project "
            f"{local_project.id} which already carries {conflict_desc}. "
            f"Resolve by merging or renaming one of the QBO sub-customers."
        )
        record_mapping_issue(
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
        self._sync_addresses(qbo_customer, updated.id)
        return updated

    def _sync_addresses(self, qbo_customer: QboCustomer, project_id: int) -> None:
        """
        Sync billing and shipping addresses from QboCustomer to ProjectAddress/Address.
        
        Args:
            qbo_customer: QboCustomer with bill_addr_id and ship_addr_id
            project_id: Database ID of the Project
        """
        # Sync billing address
        if qbo_customer.bill_addr_id:
            try:
                address = self.address_connector.sync_from_qbo_to_address(qbo_customer.bill_addr_id)
                address_id = coerce_id(address.id)
                self._ensure_project_address(project_id, address_id, ADDRESS_TYPE_BILLING)
                logger.debug(f"Synced billing address {address_id} for Project {project_id}")
            except Exception as e:
                logger.error(f"Failed to sync billing address for Project {project_id}: {e}")
        
        # Sync shipping address
        if qbo_customer.ship_addr_id:
            try:
                address = self.address_connector.sync_from_qbo_to_address(qbo_customer.ship_addr_id)
                address_id = coerce_id(address.id)
                self._ensure_project_address(project_id, address_id, ADDRESS_TYPE_SHIPPING)
                logger.debug(f"Synced shipping address {address_id} for Project {project_id}")
            except Exception as e:
                logger.error(f"Failed to sync shipping address for Project {project_id}: {e}")

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
            self._raise_project_identity_conflict_issue(
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
