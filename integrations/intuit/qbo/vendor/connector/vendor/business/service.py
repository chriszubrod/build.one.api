# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.vendor.business.model import QboVendor
from integrations.intuit.qbo.physical_address.connector.business.service import PhysicalAddressAddressConnector
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from integrations.intuit.qbo.base.field_ownership import (
    preserve_human_edited_name,
    raise_if_inactive_unmapped,
)
from integrations.intuit.qbo.base.identity_fastpath import (
    raise_concurrent_write_race,
    run_identity_fastpath_dbo_only,
)
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.locking import qbo_app_lock
from integrations.intuit.qbo.base.reconciliation_recorder import record_mapping_issue
from entities.vendor.business.service import VendorService
from entities.vendor.business.model import Vendor
from entities.vendor_address.business.service import VendorAddressService

logger = logging.getLogger(__name__)

# Address type ID for billing (typically ID 1)
ADDRESS_TYPE_BILLING = 1


def _qbo_vendor_ref(qbo_vendor: QboVendor) -> tuple[Optional[str], str]:
    """Shared qbo_id/realm_id derivation for this connector's record_mapping_issue calls."""
    return (
        str(qbo_vendor.qbo_id) if qbo_vendor.qbo_id else None,
        qbo_vendor.realm_id or "",
    )


class VendorVendorConnector:
    """
    Connector service for synchronization between QboVendor and Vendor modules.

    U-313: dbo-only identity resolution via `run_identity_fastpath_dbo_only` --
    no `qbo.VendorVendor` mapping-table read/write of any kind (mirrors
    U-300b's `AttachableAttachmentConnector` / U-307c's `ItemCostCodeConnector`
    / U-310's `CustomerCustomerConnector`, per Wave 5's "trust dbo alone" plan,
    `docs/design/wave5.md`). `dbo.Vendor.QboId`/`RealmId` (U-238a/U-290) is the
    sole identity store; dbo.Vendor's own filtered unique index + `SetVendorQboIdentity`'s
    theft-clear UPDATE guarantee at most one row holds a given identity at any
    instant, so a direct hit needs no cross-check and the old heal/adopt/dedup
    branch structure (driven by a second, independently-writable mapping
    table) no longer has anything to drift from.
    """

    def __init__(
        self,
        vendor_service: Optional[VendorService] = None,
        vendor_address_service: Optional[VendorAddressService] = None,
        address_connector: Optional[PhysicalAddressAddressConnector] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        """Initialize the VendorVendorConnector."""
        self.vendor_service = vendor_service or VendorService()
        self.vendor_address_service = vendor_address_service or VendorAddressService()
        self.address_connector = address_connector or PhysicalAddressAddressConnector()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()

    def sync_from_qbo_vendor(self, qbo_vendor: QboVendor) -> Vendor:
        """
        Sync data from QboVendor to Vendor module, via the dbo-only identity
        fast path (U-313).

        Args:
            qbo_vendor: QboVendor record

        Returns:
            Vendor: The synced Vendor record
        """
        # Normalize once: VendorService.create strips the name and dedups on the stripped
        # value, so an unstripped lookup here would miss the adopt branch and then collide
        # inside create() -- detaching the QboVendor permanently. Empty/whitespace-only
        # collapses to None so the `if vendor_name` guards below skip the lookups.
        vendor_name = (qbo_vendor.display_name or "").strip() or None

        outcome = run_identity_fastpath_dbo_only(
            qbo_id=qbo_vendor.qbo_id,
            realm_id=qbo_vendor.realm_id,
            entity_label="Vendor",
            external_label="QboVendor",
            lock_resource_label="Vendor",
            read_direct_by_qbo_identity=self.vendor_service.read_by_qbo_identity,
            apply_fields=lambda entity: self._apply_vendor_fields_and_sync(
                entity, qbo_vendor=qbo_vendor, incoming_name=vendor_name,
            ),
            on_apply_returned_none=lambda entity: raise_concurrent_write_race(
                entity_label="Vendor", entity_id=entity.id, path_label="fast path",
            ),
            resolve_candidate=lambda: self._resolve_vendor_candidate(
                qbo_vendor, vendor_name=vendor_name,
            ),
            stamp_identity=lambda candidate: self._stamp_vendor_identity(
                candidate, qbo_vendor,
            ),
        )
        if outcome.entity is None:
            # Only reachable via a concurrent-delete/ROWVERSION race inside
            # _apply_vendor_fields_and_sync's update_by_id call, or a falsy
            # qbo_id -- either way there is nothing sync-able to return; the
            # caller's per-vendor handler skips it and re-attempts next pull.
            raise RuntimeError(
                f"Failed to resolve Vendor for QboVendor {qbo_vendor.id} "
                f"(qbo_id={qbo_vendor.qbo_id}) via the dbo-only identity fast path"
            )
        return outcome.entity

    def _apply_vendor_fields_and_sync(
        self,
        vendor: Vendor,
        *,
        qbo_vendor: QboVendor,
        incoming_name: Optional[str],
    ) -> Optional[Vendor]:
        """
        `apply_fields` for the dbo-only fast path's HIT branch (direct or
        race-resolved, U-313): write the QboVendor-derived name onto an
        existing dbo-identity-matched Vendor, refresh the dbo-native
        QboActive mirror (U-275 -- every hit, even when identity itself
        hasn't changed, since a vendor can be deactivated in QBO without its
        QboId/RealmId moving), and sync addresses.

        QboId/RealmId are NOT re-stamped here -- this row's identity is
        already correct by construction (that's how `read_direct_by_qbo_identity`
        found it); the Active-only `set_qbo_identity` call below passes
        QboId/RealmId=None so the sproc's own CASE WHEN guards leave them
        untouched (no redundant re-stamp, no theft-detection re-trigger).
        The MISS branch (`_stamp_vendor_identity`) stamps the real identity
        including Active itself, so it does not call this method a second
        time for that.
        """
        resolved_name = preserve_human_edited_name(vendor.name, incoming_name)
        # Nothing to fill an empty name FROM when QBO supplied no DisplayName; [Name] is NOT NULL
        # and unguarded in UpdateVendorById, so a blank write is either SQL 515 or silent data loss.
        if resolved_name and resolved_name != vendor.name:
            vendor.name = resolved_name
            vendor = self.vendor_service.repo.update_by_id(vendor)
            logger.info(
                f"Filled Vendor {vendor.id} name from QBO DisplayName "
                f"(was empty or whitespace-only)"
            )
        else:
            # Name is this connector's ONLY mapped dbo.Vendor field, so once the curated
            # name is preserved the UPDATE is a pure no-op -- writing it anyway would churn
            # ModifiedDatetime on every vendor on every 4-hour pull.
            if vendor.name and vendor.name.strip():
                logger.debug(
                    f"Preserved curated Vendor {vendor.id} name "
                    f"(QBO DisplayName='{incoming_name}' ignored)"
                )
            else:
                logger.debug(
                    f"Skipped Vendor {vendor.id} name write "
                    f"(stored and QBO DisplayName both blank/whitespace-only)"
                )
        vendor_id = coerce_id(vendor.id)
        self.vendor_service.repo.set_qbo_identity(
            id=vendor_id, qbo_id=None, realm_id=None, active=qbo_vendor.active,
        )
        self._sync_addresses(qbo_vendor, vendor_id)
        return vendor

    def _resolve_vendor_candidate(
        self, qbo_vendor: QboVendor, *, vendor_name: Optional[str],
    ) -> Vendor:
        """
        `resolve_candidate` for the dbo-only fast path's MISS branch (U-313):
        called only under `run_identity_fastpath_dbo_only`'s create lock, once
        a genuine miss is confirmed (no dbo.Vendor currently holds this
        identity, including the re-read under lock). Adopts an existing
        Vendor by exact NAME match first -- a Vendor created locally before
        ever syncing, or a prior sync whose identity was lost, should be
        bound rather than duplicated -- the dbo-only equivalent of
        `CustomerCustomerConnector._resolve_customer_candidate`'s (U-310)
        name-match adopt step, using Vendor's own pre-existing adopt-by-name
        business logic. Falls through to a fresh create only when no name
        match exists.
        """
        # P1 guard (Codex, U-313 review): read_by_qbo_identity filters
        # IsDeleted=0, so a Vendor soft-deleted locally while still active in
        # QBO reads as a plain "miss" here -- without this check, the create/
        # adopt path below would mint a DUPLICATE active Vendor, and
        # _stamp_vendor_identity's SetVendorQboIdentity theft-clear (which has
        # no IsDeleted filter of its own) would then silently strip the
        # deleted row's QboId. Mirrors the pre-U-313 mapping-table
        # architecture's own "heal-don't-delete" discipline (never silently
        # duplicate on an identity a deleted row still holds; preserve +
        # raise instead, for a human to restore the Vendor or resolve in
        # QBO). Checked before the inactive/blank-name guards below since it
        # answers a more fundamental question ("does this identity already
        # belong to something") independent of either.
        if qbo_vendor.qbo_id:
            deleted_holder = self.vendor_service.read_deleted_by_qbo_identity(
                qbo_vendor.qbo_id, qbo_vendor.realm_id,
            )
            if deleted_holder is not None:
                self._raise_deleted_vendor_holds_identity_issue(
                    qbo_vendor=qbo_vendor, deleted_vendor=deleted_holder,
                )
                raise ValueError(
                    f"QboVendor {qbo_vendor.id} (QboId={qbo_vendor.qbo_id}, "
                    f"RealmId={qbo_vendor.realm_id}) identity is already held by soft-deleted "
                    f"Vendor {deleted_holder.id} ({deleted_holder.name}); not creating a "
                    f"duplicate. Restore the Vendor or resolve in QBO."
                )

        raise_if_inactive_unmapped(
            qbo_vendor.active, qbo_label="QboVendor", qbo_id=qbo_vendor.id, target="Vendor",
        )
        if not vendor_name:
            self._raise_blank_display_name_issue(qbo_vendor=qbo_vendor)
            raise ValueError(
                f"QboVendor {qbo_vendor.id} has a blank DisplayName and no dbo-native "
                f"identity match; cannot create or adopt a Vendor without a name."
            )

        existing = self.vendor_service.read_by_name(vendor_name)
        if existing is None:
            logger.info(f"Creating new Vendor from QboVendor {qbo_vendor.id}: name={vendor_name}")
            return self.vendor_service.create(
                name=vendor_name, abbreviation=None, is_draft=False, prefetched_by_name=None,
            )

        # The name-matched row must be re-checked for an existing, DIFFERENT
        # (QboId, RealmId) before being returned as the candidate -- the
        # dbo-only equivalent of the old mapping-table duplicate check.
        # Shared with `_stamp_vendor_identity`'s own pre-stamp re-read via
        # `_check_no_conflicting_vendor_identity`, so the two guards can't
        # drift out of sync with each other. Mirrors
        # `CustomerCustomerConnector._resolve_customer_candidate`'s Decision-2
        # guard (U-310, itself mirroring `ItemCostCodeConnector`'s, U-307c).
        self._check_no_conflicting_vendor_identity(existing, qbo_vendor)

        logger.info(
            f"Binding existing local Vendor {existing.id} ({vendor_name}) to QboVendor "
            f"{qbo_vendor.id} by name match"
        )
        # Identity stamp + address sync deliberately deferred to
        # `_stamp_vendor_identity`, which applies them atomically under the
        # candidate's own lock (mirrors CustomerCustomerConnector's Codex
        # round-2 fix, U-310, itself mirroring ItemCostCodeConnector's, U-307c).
        return existing

    def _stamp_vendor_identity(self, candidate: Vendor, qbo_vendor: QboVendor) -> Optional[Vendor]:
        """
        `stamp_identity` for the dbo-only fast path's MISS branch (U-313).

        Runs under its own app lock keyed on the CANDIDATE's vendor_id --
        NOT `run_identity_fastpath_dbo_only`'s own create lock, which is keyed
        on the qbo_id/realm_id being resolved. `_resolve_vendor_candidate`
        binds by NAME (a side-channel business key), so two different
        QboVendors (different qbo_ids -- no contention on the qbo_id-keyed
        lock upstream) could name-match onto the SAME local Vendor
        concurrently. Re-reads immediately before stamping and refuses to
        overwrite a DIFFERENT existing identity. Mirrors
        `AttachableAttachmentConnector._stamp_pulled_identity` (U-300b) /
        `ItemCostCodeConnector._stamp_cost_code_identity` (U-307c) /
        `CustomerCustomerConnector._stamp_customer_identity` (U-310) -- same
        side-channel-candidate race, same fix.

        Unlike Customer, Vendor has no other QBO-derived fields to (re-)apply
        here -- name is this family's only mapped field, and by construction
        it already matches exactly on the adopt path (`read_by_name` found an
        exact string match) or was just set correctly by `.create()` on the
        genuine-new path, so there is nothing to write beyond the identity
        stamp itself (including Active, U-275) and the address sync.
        """
        candidate_id = coerce_id(candidate.id)
        lock_resource = f"qbo_dbo_identity_stamp:Vendor:{candidate_id}"
        with qbo_app_lock(lock_resource) as got_lock:
            if not got_lock:
                raise RuntimeError(
                    f"Could not acquire identity-stamp lock for Vendor {candidate_id} "
                    f"(qbo_id={qbo_vendor.qbo_id}, realm_id={qbo_vendor.realm_id}) — holding "
                    f"for retry without stamping."
                )
            current = self.vendor_service.read_by_id(candidate_id)
            if current is None:
                return None
            self._check_no_conflicting_vendor_identity(current, qbo_vendor)
            self.vendor_service.repo.set_qbo_identity(
                id=candidate_id,
                qbo_id=qbo_vendor.qbo_id,
                realm_id=qbo_vendor.realm_id,
                active=qbo_vendor.active,
            )
            self._sync_addresses(qbo_vendor, candidate_id)
            return self.vendor_service.read_by_id(candidate_id)

    def _check_no_conflicting_vendor_identity(
        self, local_vendor: Vendor, qbo_vendor: QboVendor,
    ) -> None:
        """
        Shared guard for `_resolve_vendor_candidate`'s name-matched candidate
        and `_stamp_vendor_identity`'s pre-stamp re-read (U-313, /simplify
        reuse pass) -- ONE implementation instead of two hand-kept-in-sync
        copies, since `_stamp_vendor_identity`'s SetVendorQboIdentity theft-
        clear only protects the INCOMING (qbo_id, realm_id) pair's
        uniqueness, not `local_vendor`'s PRIOR identity; it would not stop a
        silent re-point on its own. Mirrors
        `CustomerCustomerConnector._check_no_conflicting_identity` (U-310).

        No-op when `local_vendor` has no QBO identity yet, or already carries
        this EXACT (qbo_id, realm_id) pair (a benign re-resolve). Otherwise
        records a `duplicate_qbo_vendor` reconciliation issue and raises.
        Checking QboId alone would miss a same-QboId-different-realm
        collision (QBO ids are only unique WITHIN a realm) -- both fields
        must match.

        NB: `ReadVendorByName` does not project QboId/RealmId
        (entities/vendor/sql/dbo.vendor.sql), so `_resolve_vendor_candidate`'s
        own call to this guard never actually observes a populated qbo_id
        against a REAL DB read -- it only fires when a test mocks
        `read_by_name` to return one directly (mirrors U-310's identical,
        Codex-round-1-caught gap for Customer -- see TODO.md).
        `_stamp_vendor_identity`'s call, backed by `read_by_id` (which DOES
        project QboId/RealmId), is the one that actually protects production
        -- not a redundant double-check.
        """
        existing_qbo_id = getattr(local_vendor, "qbo_id", None)
        if not existing_qbo_id or (
            existing_qbo_id == qbo_vendor.qbo_id
            and (getattr(local_vendor, "realm_id", None) or "") == (qbo_vendor.realm_id or "")
        ):
            return
        self._raise_duplicate_qbo_vendor_issue(
            qbo_vendor=qbo_vendor, local_vendor=local_vendor, existing_qbo_id=existing_qbo_id,
        )
        raise ValueError(
            f"Vendor {local_vendor.id} already carries a DIFFERENT identity "
            f"(QboId={existing_qbo_id}, RealmId={getattr(local_vendor, 'realm_id', None)}) than "
            f"incoming QboVendor {qbo_vendor.qbo_id} (realm_id={qbo_vendor.realm_id}) -- "
            f"refusing to overwrite it."
        )

    def _raise_duplicate_qbo_vendor_issue(
        self,
        *,
        qbo_vendor: QboVendor,
        local_vendor: Vendor,
        existing_qbo_id: str,
    ) -> None:
        """
        Record a name-match-vs-different-existing-identity duplicate (U-313).

        Reuses the pre-existing `duplicate_qbo_vendor` DriftType (this
        family's own conflict category, previously emitted by the old
        mapping-table-based duplicate check this replaces) rather than
        registering a new one -- semantically the same class of problem (an
        incoming QBO vendor's identity conflicting with what a local Vendor
        already carries), and keeps this unit's file scope to the connector
        file. Mirrors `CustomerCustomerConnector._raise_duplicate_qbo_customer_issue`
        (U-310).
        """
        details = (
            f"Duplicate QBO vendor detected. QboVendor {qbo_vendor.id} "
            f"(QboId={qbo_vendor.qbo_id}, DisplayName='{qbo_vendor.display_name}') "
            f"name-matches local Vendor {local_vendor.id} which already carries a "
            f"DIFFERENT identity (QboId={existing_qbo_id}). Resolve by merging or "
            f"renaming one of the QBO vendors."
        )
        qbo_id, realm_id = _qbo_vendor_ref(qbo_vendor)
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="duplicate_qbo_vendor",
            entity_type="Vendor",
            entity_public_id=str(local_vendor.public_id) if local_vendor.public_id else None,
            qbo_id=qbo_id,
            realm_id=realm_id,
            details=details,
        )

    def _raise_deleted_vendor_holds_identity_issue(
        self, *, qbo_vendor: QboVendor, deleted_vendor,
    ) -> None:
        """
        Record a soft-deleted-row-still-holds-this-identity detection on
        qbo.ReconciliationIssue (U-313 P1 guard). Triggered when a fresh
        QboVendor pull finds no ACTIVE dbo.Vendor match but a SOFT-DELETED
        one still carries the exact same (QboId, RealmId) -- preserving the
        pre-U-313 mapping-table architecture's "heal-don't-delete" discipline
        instead of silently minting a duplicate active Vendor. Critical
        because the row will re-fail every sync until a human restores the
        Vendor or resolves it in QBO.
        """
        details = (
            f"QboVendor {qbo_vendor.id} (QboId={qbo_vendor.qbo_id}, RealmId="
            f"{qbo_vendor.realm_id}, DisplayName='{qbo_vendor.display_name}') identity is "
            f"already held by soft-deleted Vendor {deleted_vendor.id} "
            f"({deleted_vendor.name}). Not creating a duplicate. Restore the Vendor or "
            f"resolve in QBO."
        )
        qbo_id, realm_id = _qbo_vendor_ref(qbo_vendor)
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="deleted_vendor_holds_identity",
            entity_type="Vendor",
            entity_public_id=(
                str(deleted_vendor.public_id) if getattr(deleted_vendor, "public_id", None) else None
            ),
            qbo_id=qbo_id,
            realm_id=realm_id,
            details=details,
        )

    def _raise_blank_display_name_issue(self, *, qbo_vendor: QboVendor) -> None:
        """
        Record a blank-DisplayName detection on qbo.ReconciliationIssue.

        Triggered when a fresh QboVendor pull has no dbo-native identity match
        and QBO supplied a blank or whitespace-only DisplayName, so the
        connector cannot create or adopt a local Vendor. Treated as critical
        because the row will re-fail every sync until a DisplayName is set in
        QBO.
        """
        details = (
            f"Blank QBO vendor DisplayName. QboVendor {qbo_vendor.id} "
            f"(QboId={qbo_vendor.qbo_id}) has a blank or whitespace-only DisplayName "
            f"and no dbo-native identity match; cannot create or adopt a Vendor without "
            f"a name. Resolve by setting a DisplayName in QBO."
        )
        qbo_id, realm_id = _qbo_vendor_ref(qbo_vendor)
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="blank_display_name_qbo_vendor",
            entity_type="Vendor",
            entity_public_id=None,
            qbo_id=qbo_id,
            realm_id=realm_id,
            details=details,
        )

    def _sync_addresses(self, qbo_vendor: QboVendor, vendor_id: int) -> None:
        """
        Sync billing address from QboVendor to VendorAddress/Address.

        Args:
            qbo_vendor: QboVendor with bill_addr_id
            vendor_id: Database ID of the Vendor
        """
        # Sync billing address
        if qbo_vendor.bill_addr_id:
            try:
                address = self.address_connector.sync_from_qbo_to_address(qbo_vendor.bill_addr_id)
                address_id = coerce_id(address.id)
                self._ensure_vendor_address(vendor_id, address_id, ADDRESS_TYPE_BILLING)
                logger.debug(f"Synced billing address {address_id} for Vendor {vendor_id}")
            except Exception as e:
                logger.error(f"Failed to sync billing address for Vendor {vendor_id}: {e}")

    def _ensure_vendor_address(self, vendor_id: int, address_id: int, address_type_id: int) -> None:
        """
        Ensure a VendorAddress record exists linking Vendor to Address.
        Creates if not exists, updates if exists with different address.

        Args:
            vendor_id: Database ID of the Vendor
            address_id: Database ID of the Address
            address_type_id: Type of address (billing)
        """
        # Filter by address_type_id among this vendor's addresses (may have multiple types)
        vendor_addresses = self.vendor_address_service.read_all_by_vendor_id(vendor_id)
        existing = None
        for va in vendor_addresses:
            va_address_type_id = coerce_id(va.address_type_id)
            if va_address_type_id == address_type_id:
                existing = va
                break

        if existing:
            existing_address_id = coerce_id(existing.address_id)
            if existing_address_id != address_id:
                # Update with new address
                existing.address_id = str(address_id)
                self.vendor_address_service.repo.update_by_id(existing)
                logger.debug(f"Updated VendorAddress {existing.id} with new address {address_id}")
        else:
            # Create new VendorAddress
            self.vendor_address_service.create(
                vendor_id=str(vendor_id),
                address_id=str(address_id),
                address_type_id=str(address_type_id)
            )
            logger.debug(f"Created VendorAddress for Vendor {vendor_id}, Address {address_id}, Type {address_type_id}")
