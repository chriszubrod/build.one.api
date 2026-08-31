# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.base.field_ownership import (
    preserve_human_edited_name,
    raise_if_inactive_unmapped,
)
from integrations.intuit.qbo.base.identity_fastpath import (
    run_identity_fastpath_dbo_only,
    stamp_dbo_identity_with_lock,
)
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.reconciliation_recorder import (
    build_duplicate_qbo_identity_conflict_desc,
    record_duplicate_identity_conflict,
)
from integrations.intuit.qbo.customer.business.model import QboCustomer
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from entities.customer.business.service import CustomerService
from entities.customer.business.model import Customer

logger = logging.getLogger(__name__)


class CustomerCustomerConnector:
    """
    Connector service for synchronization between QboCustomer and Customer modules.
    Handles parent QBO Customers (Job=false) mapping to Customer.

    U-310: dbo-only identity resolution via `run_identity_fastpath_dbo_only` --
    no `qbo.CustomerCustomer` mapping-table read/write of any kind (mirrors
    U-300b's `AttachableAttachmentConnector` / U-307c's `ItemCostCodeConnector`,
    per Wave 5's "trust dbo alone" plan, `docs/design/wave5.md`).
    `dbo.Customer.QboId`/`RealmId` (U-238c) is the sole identity store;
    dbo.Customer's own filtered unique index + `SetCustomerQboIdentity`'s
    theft-clear UPDATE guarantee at most one row holds a given identity at any
    instant, so a direct hit needs no cross-check and the old heal/adopt/dedup
    branch structure (driven by a second, independently-writable mapping
    table) no longer has anything to drift from.
    """

    def __init__(
        self,
        customer_service: Optional[CustomerService] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        """Initialize the CustomerCustomerConnector."""
        self.customer_service = customer_service or CustomerService()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()

    def sync_from_qbo_customer(self, qbo_customer: QboCustomer) -> Customer:
        """
        Sync data from QboCustomer to Customer module.

        Args:
            qbo_customer: QboCustomer record (must be a parent customer with Job=false)

        Returns:
            Customer: The synced Customer record

        Raises:
            ValueError: If the customer has Job=true (is not a parent customer)
        """
        if qbo_customer.is_job:
            raise ValueError(f"QboCustomer {qbo_customer.id} has Job=true and is not a parent customer")

        # Map QBO Customer fields to Customer module fields
        customer_name = qbo_customer.display_name or qbo_customer.company_name or ""
        customer_email = qbo_customer.primary_email_addr or ""
        customer_phone = qbo_customer.primary_phone or qbo_customer.mobile or ""

        outcome = run_identity_fastpath_dbo_only(
            qbo_id=qbo_customer.qbo_id,
            realm_id=qbo_customer.realm_id,
            entity_label="Customer",
            external_label="QboCustomer",
            lock_resource_label="Customer",
            read_direct_by_qbo_identity=self.customer_service.read_by_qbo_identity,
            apply_fields=lambda entity: self._apply_customer_fields_and_sync(
                entity, name=customer_name, email=customer_email, phone=customer_phone,
            ),
            resolve_candidate=lambda: self._resolve_customer_candidate(
                qbo_customer, name=customer_name, email=customer_email, phone=customer_phone,
            ),
            stamp_identity=lambda candidate: self._stamp_customer_identity(
                candidate, qbo_customer, name=customer_name, email=customer_email, phone=customer_phone,
            ),
        )
        if outcome.entity is None:
            # U-316: no longer race-reachable (see run_identity_fastpath_
            # dbo_only's Raises docstring) — kept as a backstop for a
            # directly-invoked falsy qbo_customer.qbo_id (this public method has
            # no guard of its own; pinned by test_customer_no_qbo_id_raises).
            # The production pull path already guards this upstream via
            # QboCustomerService._upsert_customer (U-336).
            raise RuntimeError(
                f"Failed to resolve Customer for QboCustomer {qbo_customer.id} "
                f"(qbo_id={qbo_customer.qbo_id}) via the dbo-only identity fast path"
            )
        return outcome.entity

    def _apply_customer_fields_and_sync(
        self, entity: Customer, *, name: str, email: str, phone: str,
    ) -> Optional[Customer]:
        """
        `apply_fields` for the dbo-only fast path's HIT branch (U-310): write
        the QboCustomer-derived fields onto an existing dbo-identity-matched
        Customer and persist. The single field-write path for a hit (direct
        or race-discovered) -- the pre-U-310 heal-in-place repoint path that
        also called an equivalent block is gone (nothing left to heal).
        """
        entity.name = preserve_human_edited_name(entity.name, name)
        entity.email = email
        entity.phone = phone
        return self.customer_service.repo.update_by_id(entity)

    def _resolve_customer_candidate(
        self, qbo_customer: QboCustomer, *, name: str, email: str, phone: str,
    ) -> Customer:
        """
        `resolve_candidate` for the dbo-only fast path's MISS branch (U-310):
        called only under `run_identity_fastpath_dbo_only`'s create lock, once
        a genuine miss is confirmed (no dbo.Customer currently holds this
        identity, including the re-read under lock). Adopts an existing
        Customer by NAME match first -- a Customer created locally before ever
        syncing, or a prior sync whose mapping was lost, should be bound
        rather than duplicated -- the dbo-only equivalent of
        `ItemCostCodeConnector._resolve_cost_code_candidate`'s number-match
        adopt step (U-307c), using `name` as Customer's business key the same
        way CostCode uses `number`. Falls through to a fresh create only when
        no name match exists.
        """
        raise_if_inactive_unmapped(
            qbo_customer.active, qbo_label="QboCustomer", qbo_id=qbo_customer.id, target="Customer",
        )

        existing = self.customer_service.read_by_name(name) if name else None
        if existing is None:
            logger.info(f"Creating new Customer from QboCustomer {qbo_customer.id}: name={name}")
            return self.customer_service.create(name=name, email=email, phone=phone)

        # The name-matched row must be re-checked for an existing, DIFFERENT
        # (QboId, RealmId) before being returned as the candidate -- the
        # dbo-only equivalent of the old mapping-table duplicate check.
        # `_stamp_customer_identity`'s SetCustomerQboIdentity theft-clear only
        # protects the INCOMING (qbo_id, realm_id) pair's uniqueness, not this
        # row's PRIOR identity -- it would not stop a silent re-point here.
        # Shared with `_stamp_customer_identity`'s own pre-stamp re-read via
        # `_check_no_conflicting_identity`, so the two guards can't drift out
        # of sync with each other. Mirrors
        # `ItemCostCodeConnector._resolve_cost_code_candidate`'s Decision-2
        # guard (U-307c).
        self._check_no_conflicting_identity(existing, qbo_customer)

        logger.info(
            f"Binding existing local Customer {existing.id} ({name}) to QboCustomer "
            f"{qbo_customer.id} by name match"
        )
        # Field write deliberately deferred to _stamp_customer_identity, which
        # applies it atomically with the identity stamp under the candidate's
        # own lock (mirrors ItemCostCodeConnector's Codex round-2 fix, U-307c).
        return existing

    def _stamp_customer_identity(
        self, candidate: Customer, qbo_customer: QboCustomer, *, name: str, email: str, phone: str,
    ) -> Optional[Customer]:
        """
        `stamp_identity` for the dbo-only fast path's MISS branch (U-310),
        delegating the row-scoped lock + theft-guard + write sequence to the
        shared `stamp_dbo_identity_with_lock` (U-328/U-331 —
        `docs/design/stamp-lock-helper.md`) — see that function's own
        docstring for why a SECOND lock, keyed on the CANDIDATE's
        customer_id, is needed here: `resolve_candidate` binds by NAME (a
        side-channel business key), so two different QboCustomers (different
        qbo_ids — no contention on the qbo_id-keyed lock upstream) could
        name-match onto the SAME local Customer concurrently.

        `read_by_id` (not the name-matched row `resolve_candidate` already
        has in hand) is the re-read that reliably carries QboId/RealmId
        against a real DB read (U-310 Codex round-1 P2 —
        `ReadCustomerByName` does not project them), so this is the theft-
        guard call that actually protects production. `apply_fields` writes
        name/email/phone (U-219: name is raw, bypassing
        `preserve_human_edited_name` — adopt-by-name always assigns the
        incoming value) and feeds `update_by_id`'s return value into the
        shared helper's own None-guard (a ROWVERSION race must not silently
        proceed to stamp identity on a row whose field write never took).
        `on_conflict` keeps only the reconciliation-recording half of the
        former `_check_no_conflicting_identity` call — the raise itself now
        lives in the shared helper.
        """
        def _apply_fields(c: Customer) -> Optional[Customer]:
            c.name = name
            c.email = email
            c.phone = phone
            return self.customer_service.repo.update_by_id(c)

        candidate_id = coerce_id(candidate.id)
        return stamp_dbo_identity_with_lock(
            candidate_id=candidate_id,
            entity_label="Customer",
            qbo_id=qbo_customer.qbo_id,
            realm_id=qbo_customer.realm_id,
            read_by_id=self.customer_service.read_by_id,
            apply_fields=_apply_fields,
            write_identity=lambda c: self.customer_service.repo.set_qbo_identity(
                id=c.id, qbo_id=qbo_customer.qbo_id, realm_id=qbo_customer.realm_id,
            ),
            on_conflict=lambda c: self._record_duplicate_qbo_customer_issue(
                qbo_customer=qbo_customer, local_customer=c, existing_qbo_id=c.qbo_id,
            ),
        )

    def _check_no_conflicting_identity(
        self, local_customer: Customer, qbo_customer: QboCustomer,
    ) -> None:
        """
        Shared guard for `_resolve_customer_candidate`'s name-matched
        candidate and `_stamp_customer_identity`'s pre-stamp re-read (U-310,
        Codex round-1 P2 fix) -- ONE implementation instead of two hand-kept-
        in-sync copies, since `_stamp_customer_identity`'s SetCustomerQboIdentity
        theft-clear only protects the INCOMING (qbo_id, realm_id) pair's
        uniqueness, not `local_customer`'s PRIOR identity; it would not stop a
        silent re-point on its own.

        No-op when `local_customer` has no QBO identity yet, or already
        carries this EXACT (qbo_id, realm_id) pair (a benign re-resolve).
        Otherwise records a `customer_identity_conflict` reconciliation issue
        and raises. Checking QboId alone would miss a same-QboId-different-
        realm collision (QBO ids are only unique WITHIN a realm) -- both
        fields must match. Mirrors
        `ItemCostCodeConnector._resolve_cost_code_candidate`'s Decision-2
        guard (U-307c).
        """
        existing_qbo_id = getattr(local_customer, "qbo_id", None)
        if not existing_qbo_id or (
            existing_qbo_id == qbo_customer.qbo_id
            and (getattr(local_customer, "realm_id", None) or "") == (qbo_customer.realm_id or "")
        ):
            return
        self._record_duplicate_qbo_customer_issue(
            qbo_customer=qbo_customer,
            local_customer=local_customer,
            existing_qbo_id=existing_qbo_id,
        )
        raise ValueError(
            f"Customer {local_customer.id} already carries a DIFFERENT identity "
            f"(QboId={existing_qbo_id}, RealmId={getattr(local_customer, 'realm_id', None)}) than "
            f"incoming QboCustomer {qbo_customer.qbo_id} (realm_id={qbo_customer.realm_id}) — "
            f"refusing to overwrite it."
        )

    def _record_duplicate_qbo_customer_issue(
        self,
        *,
        qbo_customer: QboCustomer,
        local_customer: Customer,
        existing_qbo_id: str,
    ) -> None:
        """
        Name-match-vs-different-existing-identity duplicate (U-310). Reuses
        `customer_identity_conflict` (this family's own category, previously emitted by
        the deleted mapping-table `_record_identity_mapping_conflict_issue`).
        """
        existing_realm_id = getattr(local_customer, "realm_id", None)
        conflict_desc = build_duplicate_qbo_identity_conflict_desc(
            existing_qbo_id=existing_qbo_id,
            incoming_qbo_id=qbo_customer.qbo_id,
            existing_realm_id=existing_realm_id,
            incoming_realm_id=qbo_customer.realm_id,
        )
        details = (
            f"Duplicate QBO customer detected. QboCustomer {qbo_customer.id} "
            f"(Name='{qbo_customer.display_name}') name-matches local Customer "
            f"{local_customer.id} which already carries {conflict_desc}. "
            f"Resolve by merging or renaming one of the QBO customers."
        )
        record_duplicate_identity_conflict(
            self.reconciliation_repo,
            drift_type="customer_identity_conflict",
            entity_type="Customer",
            entity_public_id=str(local_customer.public_id) if local_customer.public_id else None,
            qbo_id=str(qbo_customer.qbo_id) if qbo_customer.qbo_id else None,
            realm_id=qbo_customer.realm_id or "",
            details=details,
        )
