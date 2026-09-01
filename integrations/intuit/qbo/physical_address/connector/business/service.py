# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.base.identity_fastpath import (
    run_identity_fastpath_dbo_only,
    stamp_dbo_identity_with_lock,
)
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.reconciliation_recorder import (
    build_duplicate_qbo_identity_conflict_desc,
    record_duplicate_identity_conflict,
)
from integrations.intuit.qbo.physical_address.business.service import QboPhysicalAddressService
from integrations.intuit.qbo.physical_address.business.model import QboPhysicalAddress
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from entities.address.business.service import AddressService
from entities.address.business.model import Address

logger = logging.getLogger(__name__)


class PhysicalAddressAddressConnector:
    """
    Connector service for synchronization between QboPhysicalAddress and Address modules.

    U-351: dbo-only identity resolution via `run_identity_fastpath_dbo_only` --
    no `qbo.PhysicalAddressAddress` mapping-table read/write of any kind (the
    second family of the U-349 program, mirroring U-350's
    `CompanyInfoCompanyConnector` pattern-setter and U-310's
    `CustomerCustomerConnector` / U-313's `VendorVendorConnector`, per Wave
    5's "trust dbo alone" plan, `docs/design/u349-qbo-mapping-table-retirement.md`).
    `dbo.Address.QboId`/`RealmId` (U-238c/U-277) is the sole identity store;
    dbo.Address's own filtered unique index + `SetAddressQboIdentity`'s
    theft-clear UPDATE guarantee at most one row holds a given identity at
    any instant, so a direct hit needs no cross-check and the old
    heal/adopt/dedup branch structure (driven by a second,
    independently-writable mapping table) no longer has anything to drift
    from. The dead `sync_from_address_to_qbo` push path (zero callers,
    confirmed at Gate-1) was removed alongside it.

    Field Mapping:
        QboPhysicalAddress.line1 <-> Address.street_one
        QboPhysicalAddress.line2 <-> Address.street_two
        QboPhysicalAddress.city <-> Address.city
        QboPhysicalAddress.country_sub_division_code <-> Address.state
        QboPhysicalAddress.postal_code <-> Address.zip
    """

    def __init__(
        self,
        address_service: Optional[AddressService] = None,
        qbo_physical_address_service: Optional[QboPhysicalAddressService] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        """Initialize the PhysicalAddressAddressConnector."""
        self.address_service = address_service or AddressService()
        self.qbo_physical_address_service = qbo_physical_address_service or QboPhysicalAddressService()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()

    def sync_from_qbo_to_address(self, qbo_physical_address_id: int) -> Address:
        """
        Sync data from QboPhysicalAddress to Address module, via the dbo-only
        identity fast path (U-351).

        Args:
            qbo_physical_address_id: Database ID of QboPhysicalAddress record

        Returns:
            Address: The synced Address record
        """
        qbo_physical_address_repo = self.qbo_physical_address_service.repo
        qbo_physical_address = qbo_physical_address_repo.read_by_id(qbo_physical_address_id)

        if not qbo_physical_address:
            raise ValueError(f"QboPhysicalAddress with ID {qbo_physical_address_id} not found")

        street_one = qbo_physical_address.line1
        street_two = qbo_physical_address.line2
        city = qbo_physical_address.city
        state = qbo_physical_address.country_sub_division_code
        zip_code = qbo_physical_address.postal_code
        # No connector-level realm fallback here (unlike CompanyInfoCompanyConnector's
        # U-277 fallback) -- sync_from_qbo_to_address has never taken a separate realm
        # parameter; realm comes straight from the staging row.
        realm_id = qbo_physical_address.realm_id

        outcome = run_identity_fastpath_dbo_only(
            qbo_id=qbo_physical_address.qbo_id,
            realm_id=realm_id,
            entity_label="Address",
            external_label="QboPhysicalAddress",
            lock_resource_label="Address",
            read_direct_by_qbo_identity=self.address_service.read_by_qbo_identity,
            apply_fields=lambda entity: self._apply_address_fields_and_sync(
                entity, street_one=street_one, street_two=street_two, city=city, state=state, zip_code=zip_code,
            ),
            resolve_candidate=lambda: self._resolve_address_candidate(
                qbo_physical_address, street_one=street_one, street_two=street_two, city=city, state=state,
                zip_code=zip_code,
            ),
            stamp_identity=lambda candidate: self._stamp_address_identity(
                candidate, qbo_physical_address, street_one=street_one, street_two=street_two, city=city,
                state=state, zip_code=zip_code,
            ),
        )
        if outcome.entity is None:
            # No longer race-reachable in practice (see run_identity_fastpath_
            # dbo_only's Raises docstring) — kept as a backstop for a directly
            # invoked falsy qbo_physical_address.qbo_id, mirroring every
            # sibling connector's identical guard (U-350/U-310/U-313/U-311).
            raise RuntimeError(
                f"Failed to resolve Address for QboPhysicalAddress {qbo_physical_address.id} "
                f"(qbo_id={qbo_physical_address.qbo_id}) via the dbo-only identity fast path"
            )
        return outcome.entity

    def _apply_address_fields_and_sync(
        self, entity: Address, *, street_one: str, street_two: str, city: str, state: str, zip_code: str,
    ) -> Optional[Address]:
        """
        `apply_fields` for the dbo-only fast path's HIT branch (U-351): write
        the QboPhysicalAddress-derived fields onto an existing dbo-identity-
        matched Address and persist. QBO is source of truth — always
        overwrites, same as the pre-U-351 fast path's own behavior. Blank/None
        values sanitize to `""` — the pre-existing U-277 fast path already did
        this (unlike CompanyInfoCompanyConnector's HIT branch, which had a
        legacy gap here) — required because street_one/street_two/city/state/
        zip are all `NOT NULL` columns.

        Returns None on a ROWVERSION-race/concurrent-delete `update_by_id`
        miss (U-291) — `run_identity_fastpath_dbo_only`'s own `_apply()`
        raises `raise_concurrent_write_race` unconditionally whenever
        `apply_fields` returns None, so this method staying silent on a miss
        is what keeps that single raise as the ONE place the guarantee lives
        (mirrors every sibling connector's identical HIT-branch shape).
        """
        entity.street_one = street_one or ""
        entity.street_two = street_two or ""
        entity.city = city or ""
        entity.state = state or ""
        entity.zip = zip_code or ""
        return self.address_service.repo.update_by_id(entity)

    def _resolve_address_candidate(
        self,
        qbo_physical_address: QboPhysicalAddress,
        *,
        street_one: str,
        street_two: str,
        city: str,
        state: str,
        zip_code: str,
    ) -> Address:
        """
        `resolve_candidate` for the dbo-only fast path's MISS branch (U-351):
        called only under `run_identity_fastpath_dbo_only`'s create lock, once
        a genuine miss is confirmed (no dbo.Address currently holds this
        identity, including the re-read under lock). Adopts an existing
        Address by (street_one, city) match first — the pre-U-351 fast path's
        own Step 2 by-street/city dedup safety net, preserved WITHOUT its
        mapping read/repair — before falling through to a fresh create.
        Mirrors `CompanyInfoCompanyConnector._resolve_company_candidate` (U-350).

        Reads `qbo_physical_address.realm_id` directly rather than taking a
        separate `realm_id` parameter (unlike the Company mirror this was
        copied from) — `sync_from_qbo_to_address` has no connector-level realm
        fallback, so the two values can never diverge here; a second parameter
        would just be redundant threaded state (Pass-2 simplification).
        """
        existing = (
            self.address_service.read_by_street_one_and_city(street_one=street_one, city=city)
            if street_one and city
            else None
        )
        if existing is None:
            logger.info(
                f"No existing Address found. Creating new Address from QboPhysicalAddress {qbo_physical_address.id}"
            )
            return self.address_service.create(
                street_one=street_one or "", street_two=street_two or "", city=city or "",
                state=state or "", zip=zip_code or "",
            )

        # The street/city-matched row must be re-checked for an existing,
        # DIFFERENT (QboId, RealmId) before being returned as the candidate --
        # the dbo-only equivalent of the old mapping-table duplicate check.
        # Shared with `_stamp_address_identity`'s own pre-stamp re-read via
        # `_check_no_conflicting_address_identity`, so the two guards can't
        # drift out of sync with each other. Mirrors
        # `CompanyInfoCompanyConnector._resolve_company_candidate`'s
        # Decision-2-style guard (U-350).
        self._check_no_conflicting_address_identity(existing, qbo_physical_address)

        logger.info(
            f"Binding existing local Address {existing.id} ({street_one}, {city}) to "
            f"QboPhysicalAddress {qbo_physical_address.id} by street/city match"
        )
        # Field write deliberately deferred to _stamp_address_identity, which
        # applies it atomically with the identity stamp under the candidate's
        # own lock (mirrors CompanyInfoCompanyConnector's Codex round-2 fix, U-350).
        return existing

    def _stamp_address_identity(
        self,
        candidate: Address,
        qbo_physical_address: QboPhysicalAddress,
        *,
        street_one: str,
        street_two: str,
        city: str,
        state: str,
        zip_code: str,
    ) -> Optional[Address]:
        """
        `stamp_identity` for the dbo-only fast path's MISS branch (U-351),
        delegating the row-scoped lock + theft-guard + write sequence to the
        shared `stamp_dbo_identity_with_lock` (U-328/U-331 —
        `docs/design/stamp-lock-helper.md`) — see that function's own
        docstring for why a SECOND lock, keyed on the CANDIDATE's address_id,
        is needed here: `_resolve_address_candidate` binds by (street_one,
        city) (a side-channel business key), so two different
        QboPhysicalAddress syncs could street/city-match onto the SAME local
        Address concurrently. Mirrors
        `CompanyInfoCompanyConnector._stamp_company_identity` (U-350).

        `apply_fields` writes the address fields unconditionally (QBO is
        source of truth) with the same blank-to-`""` sanitization as
        `_apply_address_fields_and_sync` and `_resolve_address_candidate`'s
        own `.create(..., street_one=street_one or "", ...)` — required here
        too since this closure ALSO runs right after a genuine-miss
        `.create()` call, and a raw `None`/blank would immediately overwrite
        that call's already-sanitized fields with a NULL, failing
        `UpdateAddressById`'s `NOT NULL` columns (the class of bug
        CompanyInfoCompanyConnector's Codex xhigh round-1 P1 found).
        `write_identity` delegates to `create_mapping`, which now only stamps
        dbo identity — there is no mapping row left to write. `on_conflict`
        keeps only the reconciliation-recording half of the former
        `_check_no_conflicting_address_identity` call — the raise itself now
        lives in the shared helper.
        """
        def _apply_fields(a: Address) -> Optional[Address]:
            a.street_one = street_one or ""
            a.street_two = street_two or ""
            a.city = city or ""
            a.state = state or ""
            a.zip = zip_code or ""
            return self.address_service.repo.update_by_id(a)

        candidate_id = coerce_id(candidate.id)
        return stamp_dbo_identity_with_lock(
            candidate_id=candidate_id,
            entity_label="Address",
            qbo_id=qbo_physical_address.qbo_id,
            realm_id=qbo_physical_address.realm_id,
            read_by_id=self.address_service.read_by_id,
            apply_fields=_apply_fields,
            write_identity=lambda a: self.create_mapping(
                address_id=a.id,
                qbo_physical_address_id=qbo_physical_address.id,
                qbo_id=qbo_physical_address.qbo_id,
                realm_id=qbo_physical_address.realm_id,
            ),
            on_conflict=lambda a: self._record_duplicate_qbo_address_issue(
                qbo_physical_address=qbo_physical_address, local_address=a, existing_qbo_id=a.qbo_id,
            ),
        )

    def _check_no_conflicting_address_identity(
        self, local_address: Address, qbo_physical_address: QboPhysicalAddress,
    ) -> None:
        """
        Shared guard for `_resolve_address_candidate`'s street/city-matched
        candidate and `_stamp_address_identity`'s pre-stamp re-read (U-351) --
        ONE implementation instead of two hand-kept-in-sync copies, since
        `_stamp_address_identity`'s SetAddressQboIdentity theft-clear only
        protects the INCOMING (qbo_id, realm_id) pair's uniqueness, not
        `local_address`'s PRIOR identity; it would not stop a silent
        re-point on its own. Mirrors
        `CompanyInfoCompanyConnector._check_no_conflicting_company_identity` (U-350).

        No-op when `local_address` has no QBO identity yet, or already
        carries this EXACT (qbo_id, realm_id) pair (a benign re-resolve).
        Otherwise records an `address_identity_conflict` reconciliation issue
        (this family's own pre-existing DriftType) and raises. Checking QboId
        alone would miss a same-QboId-different-realm collision (QBO ids are
        only unique WITHIN a realm) — both fields must match.
        """
        existing_qbo_id = getattr(local_address, "qbo_id", None)
        if not existing_qbo_id or (
            existing_qbo_id == qbo_physical_address.qbo_id
            and (getattr(local_address, "realm_id", None) or "") == (qbo_physical_address.realm_id or "")
        ):
            return
        self._record_duplicate_qbo_address_issue(
            qbo_physical_address=qbo_physical_address, local_address=local_address, existing_qbo_id=existing_qbo_id,
        )
        raise ValueError(
            f"Address {local_address.id} already carries a DIFFERENT identity "
            f"(QboId={existing_qbo_id}, RealmId={getattr(local_address, 'realm_id', None)}) than "
            f"incoming QboPhysicalAddress {qbo_physical_address.qbo_id} "
            f"(realm_id={qbo_physical_address.realm_id}) — refusing to overwrite it."
        )

    def _record_duplicate_qbo_address_issue(
        self,
        *,
        qbo_physical_address: QboPhysicalAddress,
        local_address: Address,
        existing_qbo_id: str,
    ) -> None:
        """
        Street/city-match-vs-different-existing-identity duplicate (U-351).
        Reuses `address_identity_conflict` (this family's own pre-existing
        DriftType, previously emitted by the deleted mapping-table
        `_record_identity_mapping_conflict_issue`). Mirrors
        `CompanyInfoCompanyConnector._record_duplicate_qbo_company_issue` (U-350).

        Records `qbo_physical_address.realm_id` — the same value the conflict
        check itself compared against — `sync_from_qbo_to_address` has no
        connector-level realm fallback (unlike CompanyInfoCompanyConnector's
        U-277 fallback), so there is no raw-vs-effective divergence to guard
        against here (the class of bug CompanyInfoCompanyConnector's Codex
        xhigh round-1 P2 found).
        """
        existing_realm_id = getattr(local_address, "realm_id", None)
        conflict_desc = build_duplicate_qbo_identity_conflict_desc(
            existing_qbo_id=existing_qbo_id,
            incoming_qbo_id=qbo_physical_address.qbo_id,
            existing_realm_id=existing_realm_id,
            incoming_realm_id=qbo_physical_address.realm_id,
        )
        details = (
            f"Duplicate QBO address detected. QboPhysicalAddress {qbo_physical_address.id} "
            f"name-matches local Address {local_address.id} which already carries {conflict_desc}. "
            f"Resolve by merging or renaming one of the QBO addresses."
        )
        record_duplicate_identity_conflict(
            self.reconciliation_repo,
            drift_type="address_identity_conflict",
            entity_type="Address",
            entity_public_id=str(local_address.public_id) if local_address.public_id else None,
            qbo_id=str(qbo_physical_address.qbo_id) if qbo_physical_address.qbo_id else None,
            realm_id=qbo_physical_address.realm_id or "",
            details=details,
        )

    def create_mapping(
        self,
        address_id: int,
        qbo_physical_address_id: int,
        *,
        qbo_id: Optional[str],
        realm_id: Optional[str],
    ) -> None:
        """
        Bind an Address to its QBO identity by stamping
        dbo.Address.QboId/RealmId (U-351).

        `dbo.Address.QboId`/`RealmId` is the SOLE identity store — this no
        longer reads or writes a `qbo.PhysicalAddressAddress` mapping row
        (that table is retired). `qbo_physical_address_id` stays in the
        signature for the caller's symmetry but is no longer persisted
        anywhere.

        The sole caller is `_stamp_address_identity`, which reaches this only
        under `stamp_dbo_identity_with_lock`'s own theft-guard — already
        refusing to overwrite a DIFFERENT existing identity — so the former
        mapping-table 1:1 validations are redundant and were removed with the
        mapping write. Mirrors `CompanyInfoCompanyConnector.create_mapping` (U-350).
        """
        self.address_service.repo.set_qbo_identity(
            id=address_id,
            qbo_id=qbo_id,
            realm_id=realm_id,
        )
