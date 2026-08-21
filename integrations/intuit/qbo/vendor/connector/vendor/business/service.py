# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.vendor.connector.vendor.business.model import VendorVendor
from integrations.intuit.qbo.vendor.connector.vendor.persistence.repo import VendorVendorRepository
from integrations.intuit.qbo.vendor.business.model import QboVendor
from integrations.intuit.qbo.physical_address.connector.business.service import PhysicalAddressAddressConnector
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from integrations.intuit.qbo.base.field_ownership import (
    preserve_human_edited_name,
    raise_if_inactive_unmapped,
)
from integrations.intuit.qbo.base.identity_fastpath import run_identity_fastpath
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.reconciliation_recorder import record_mapping_issue
from entities.vendor.business.service import VendorService
from entities.vendor.business.model import Vendor
from entities.vendor_address.business.service import VendorAddressService
from shared.database import DatabaseConstraintError
from shared.db_constraints import UNIQUE

logger = logging.getLogger(__name__)

# Address type ID for billing (typically ID 1)
ADDRESS_TYPE_BILLING = 1

_PREFETCH_UNSET = object()


def _qbo_vendor_ref(qbo_vendor: QboVendor) -> tuple[Optional[str], str]:
    """Shared qbo_id/realm_id derivation for this connector's record_mapping_issue calls."""
    return (
        str(qbo_vendor.qbo_id) if qbo_vendor.qbo_id else None,
        qbo_vendor.realm_id or "",
    )


class VendorVendorConnector:
    """
    Connector service for synchronization between QboVendor and Vendor modules.
    """

    def __init__(
        self,
        mapping_repo: Optional[VendorVendorRepository] = None,
        vendor_service: Optional[VendorService] = None,
        vendor_address_service: Optional[VendorAddressService] = None,
        address_connector: Optional[PhysicalAddressAddressConnector] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        """Initialize the VendorVendorConnector."""
        self.mapping_repo = mapping_repo or VendorVendorRepository()
        self.vendor_service = vendor_service or VendorService()
        self.vendor_address_service = vendor_address_service or VendorAddressService()
        self.address_connector = address_connector or PhysicalAddressAddressConnector()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()

    def sync_from_qbo_vendor(self, qbo_vendor: QboVendor) -> Vendor:
        """
        Sync data from QboVendor to Vendor module.
        
        This method:
        1. Checks if a mapping exists
        2. Creates or updates the Vendor accordingly
        
        Args:
            qbo_vendor: QboVendor record
        
        Returns:
            Vendor: The synced Vendor record
        """
        # Normalize once: VendorService.create strips the name and dedups on the stripped
        # value, so an unstripped lookup here would miss the adopt branch and then collide
        # inside create() — detaching the QboVendor permanently. Empty/whitespace-only
        # collapses to None so the `if vendor_name` guards below skip the lookups.
        vendor_name = (qbo_vendor.display_name or "").strip() or None

        # U-290 (Phase-4, header/reference repoint): resolve identity directly against
        # dbo.Vendor's native QboId/RealmId (U-238a/238c) before falling back to the
        # qbo.VendorVendor mapping-table hop below. Every Vendor synced even once
        # already carries this identity (set_qbo_identity is called on both the create
        # path and the legacy update path below), so this covers the steady-state case
        # without touching qbo.Vendor at all. Mirrors
        # VendorCreditBillCreditConnector.sync_from_qbo_vendor_credit (U-278) exactly,
        # via the shared run_identity_fastpath() helper (U-287) — conflict->RAISE is
        # structural there, not something this connector can opt out of.
        #
        # The mapping-table state is checked BEFORE any write, not after: writing to
        # the dbo-identity-matched Vendor first and detecting a conflict afterward
        # would corrupt that Vendor's data in the case where the mapping table — not
        # dbo identity — is actually still the correct side (U-276 round-3 finding).
        def _on_apply_returned_none(direct: Vendor) -> None:
            # _apply_vendor_fields_and_sync uses the repo-level update_by_id, which
            # RAISERRORs (not returns None) on a RowVersion mismatch or missing row, so
            # this should be unreachable in practice. Raising RuntimeError rather than
            # letting a None silently count as success mirrors the U-287 fix for
            # CustomerCustomerConnector — a plain ValueError here would classify as a
            # permanent skip and advance the watermark past an unmapped Vendor; a
            # RuntimeError does not.
            raise RuntimeError(
                f"Vendor {direct.id} update returned None during the QboVendor "
                f"{qbo_vendor.id} dbo-identity fast path (missing-mapping branch) — "
                f"treating as a failure, not a silent success."
            )

        fastpath_outcome = run_identity_fastpath(
            qbo_id=qbo_vendor.qbo_id,
            realm_id=qbo_vendor.realm_id,
            external_id=qbo_vendor.id,
            entity_label="Vendor",
            external_label="QboVendor",
            mapping_label="VendorVendor",
            read_direct_by_qbo_identity=self.vendor_service.read_by_qbo_identity,
            read_by_local_id=self.mapping_repo.read_by_vendor_id,
            read_by_external_id=self.mapping_repo.read_by_qbo_vendor_id,
            external_id_attr="qbo_vendor_id",
            record_conflict_issue=lambda entity, by_local, by_external: (
                self._raise_identity_mapping_conflict_issue(
                    qbo_vendor=qbo_vendor,
                    dbo_vendor_id=coerce_id(entity.id),
                    local_side_mapping=by_local,
                    qbo_side_mapping=by_external,
                )
            ),
            conflict_message=lambda entity: (
                f"VendorVendor identity conflict for QboVendor {qbo_vendor.qbo_id} "
                f"(id={qbo_vendor.id}): dbo.Vendor {entity.id} already carries this "
                f"identity but the mapping table disagrees. Not auto-repointed; see "
                f"the recorded reconciliation issue. Skipping until a human resolves it."
            ),
            create_mapping=lambda local_id: self.mapping_repo.create(
                vendor_id=local_id, qbo_vendor_id=qbo_vendor.id,
            ),
            apply_fields=lambda entity: self._apply_vendor_fields_and_sync(
                entity, qbo_vendor=qbo_vendor, incoming_name=vendor_name,
            ),
            on_apply_returned_none=_on_apply_returned_none,
        )
        if fastpath_outcome.hit:
            if fastpath_outcome.entity is not None:
                # QboActive is a dbo-native mirror (U-275) that must stay current every
                # sync tick even when identity itself hasn't changed (a vendor can be
                # deactivated in QBO without its QboId/RealmId moving). Mirrors the same
                # fix ItemSubCostCodeConnector (U-289, concurrent this session) applied for
                # its own QboActive mirror — a strictly more correct pattern than U-282
                # (PaymentTerm)'s accepted staleness tradeoff documented above, adopted here
                # instead since Vendor shares the same "family with an Active mirror"
                # shape. QboId/RealmId are passed as None so the sproc's own CASE WHEN
                # guards leave them untouched (no redundant re-stamp, no theft-detection
                # re-trigger); only QboActive's CASE WHEN branch fires, and only when it
                # actually changed.
                self.vendor_service.repo.set_qbo_identity(
                    id=coerce_id(fastpath_outcome.entity.id),
                    qbo_id=None,
                    realm_id=None,
                    active=qbo_vendor.active,
                )
            return fastpath_outcome.entity

        mapping = self.mapping_repo.read_by_qbo_vendor_id(qbo_vendor.id)

        if mapping:
            vendor = self.vendor_service.read_by_id(mapping.vendor_id)
            if vendor:
                logger.info(f"Updating existing Vendor {vendor.id} from QboVendor {qbo_vendor.id}")
                updated = self._apply_vendor_fields_and_sync(
                    vendor, qbo_vendor=qbo_vendor, incoming_name=vendor_name
                )
                self.vendor_service.repo.set_qbo_identity(
                    id=coerce_id(updated.id),
                    qbo_id=qbo_vendor.qbo_id,
                    realm_id=qbo_vendor.realm_id,
                    active=qbo_vendor.active,
                )
                return updated

            # HEAL — mapping exists but the bound Vendor reads empty.
            # NEVER delete the mapping and NEVER fall through to create (audit P1-08).
            # dbo.ReadVendorById filters IsDeleted = 0, so a locally soft-deleted vendor lands
            # here DETERMINISTICALLY, not just on a transient read.
            replacement = self.vendor_service.read_by_name(vendor_name) if vendor_name else None
            if replacement:
                replacement_id = coerce_id(replacement.id)
                existing_map = self.mapping_repo.read_by_vendor_id(replacement_id)
                if existing_map and existing_map.qbo_vendor_id != qbo_vendor.id:
                    # Name-matched vendor is already bound to a DIFFERENT QboVendor — a genuine
                    # QBO-side duplicate vendor. Repointing would break the 1:1 mapping.
                    self._raise_duplicate_qbo_vendor_issue(
                        qbo_vendor=qbo_vendor,
                        local_vendor=replacement,
                        existing_mapping=existing_map,
                    )
                    raise ValueError(
                        f"VendorVendor mapping {mapping.id} points at missing Vendor "
                        f"{mapping.vendor_id}; name match Vendor {replacement_id} is already "
                        f"bound to QboVendor {existing_map.qbo_vendor_id}."
                    )
                if mapping.vendor_id != replacement_id:
                    # Repoint IN PLACE via update_by_id — no delete, no window.
                    old_vendor_id = mapping.vendor_id
                    mapping.vendor_id = replacement_id
                    self.mapping_repo.update_by_id(mapping)
                    logger.info(
                        f"Healed VendorVendor mapping {mapping.id}: repointed QboVendor "
                        f"{qbo_vendor.id} from missing Vendor {old_vendor_id} to Vendor "
                        f"{replacement_id} ({vendor_name})"
                    )
                self.vendor_service.repo.set_qbo_identity(
                    id=replacement_id,
                    qbo_id=qbo_vendor.qbo_id,
                    realm_id=qbo_vendor.realm_id,
                    active=qbo_vendor.active,
                )
                return self._apply_vendor_fields_and_sync(
                    replacement, qbo_vendor=qbo_vendor, incoming_name=vendor_name
                )

            # No replacement resolvable — record and RAISE, mutating nothing. Mapping is preserved
            # (QboVendor<->Vendor binding intact; no duplicate minted). sync_qbo_vendor advances the
            # watermark unconditionally (hold booked U-217), so this is a permanent skip until QBO
            # touches the vendor again or a full re-sync. The ReconciliationIssue row is the durable
            # follow-up — recovery is restore the soft-deleted Vendor or repoint the mapping by hand.
            self._raise_missing_vendor_issue(qbo_vendor=qbo_vendor, mapping=mapping)
            raise ValueError(
                f"VendorVendor mapping {mapping.id} points at missing Vendor "
                f"{mapping.vendor_id} and no local Vendor named \"{vendor_name}\" could be "
                f"resolved for QboVendor {qbo_vendor.id}; preserving mapping, skipping."
            )

        # Deactivation guard (U-219): after the mapping lookup, before the adopt-by-name bind.
        raise_if_inactive_unmapped(
            qbo_vendor.active, qbo_label="QboVendor", qbo_id=qbo_vendor.id, target="Vendor"
        )
        if not vendor_name:
            self._raise_blank_display_name_issue(qbo_vendor=qbo_vendor)
            raise ValueError(
                f"QboVendor {qbo_vendor.id} has a blank DisplayName and no local mapping; "
                f"cannot create or adopt a Vendor without a name."
            )
        # No mapping. Adopt an existing unmapped local Vendor by exact name BEFORE creating —
        # VendorService.create refuses a duplicate name, so without this a name collision detaches
        # the QBO vendor permanently (audit P1-08's second half).
        existing_local = self.vendor_service.read_by_name(vendor_name)
        if existing_local:
            existing_local_id = coerce_id(existing_local.id)
            existing_map_for_local = self.mapping_repo.read_by_vendor_id(existing_local_id)
            if existing_map_for_local:
                self._raise_duplicate_qbo_vendor_issue(
                    qbo_vendor=qbo_vendor,
                    local_vendor=existing_local,
                    existing_mapping=existing_map_for_local,
                )
                raise ValueError(
                    f"QboVendor {qbo_vendor.id} name-matches local Vendor {existing_local_id} "
                    f"which is already bound to QboVendor {existing_map_for_local.qbo_vendor_id}."
                )
            logger.info(
                f"Binding existing local Vendor {existing_local_id} ({vendor_name}) "
                f"to QboVendor {qbo_vendor.id} by name match"
            )
            self.create_mapping(
                vendor_id=existing_local_id,
                qbo_vendor_id=qbo_vendor.id,
                qbo_id=qbo_vendor.qbo_id,
                realm_id=qbo_vendor.realm_id,
                active=qbo_vendor.active,
                prefetched_by_vendor=None,
                prefetched_by_qbo_vendor=None,
            )
            self._sync_addresses(qbo_vendor, existing_local_id)
            return existing_local

        # Create a new Vendor + mapping.
        logger.info(f"Creating new Vendor from QboVendor {qbo_vendor.id}: name={vendor_name}")
        vendor = self.vendor_service.create(
            name=vendor_name,
            abbreviation=None,
            is_draft=False,
            prefetched_by_name=None,
        )
        vendor_id = coerce_id(vendor.id)
        try:
            self.create_mapping(
                vendor_id=vendor_id,
                qbo_vendor_id=qbo_vendor.id,
                qbo_id=qbo_vendor.qbo_id,
                realm_id=qbo_vendor.realm_id,
                active=qbo_vendor.active,
                prefetched_by_vendor=None,
                prefetched_by_qbo_vendor=None,
            )
            logger.info(f"Created mapping: Vendor {vendor_id} <-> QboVendor {qbo_vendor.id}")
        except ValueError as e:
            # Do NOT swallow (audit P1-08). Surface it so the caller's per-item handler logs + skips.
            # The orphaned Vendor can be adopted by the name-match branch on a later pull that includes
            # this vendor — after unconditional watermark advance that means the next QBO-side touch or
            # a full re-sync, not necessarily the next scheduler tick.
            logger.error(
                f"Mapping creation failed after Vendor {vendor_id} create "
                f"(QboVendor {qbo_vendor.id}): {e}. Orphaned Vendor may be adopted on a later pull "
                f"that includes this vendor (QBO-side touch or full re-sync), not necessarily the "
                f"next scheduler tick."
            )
            raise
        self._sync_addresses(qbo_vendor, vendor_id)
        return vendor

    def _apply_vendor_fields_and_sync(
        self,
        vendor: Vendor,
        *,
        qbo_vendor: QboVendor,
        incoming_name: Optional[str],
    ) -> Vendor:
        """
        Write the QboVendor-derived name onto an existing Vendor when appropriate,
        persist it, and sync addresses. Shared by the normal existing-mapping update path,
        the heal-in-place repoint path, and the U-290 dbo-identity fast path, so the
        QboVendor->Vendor field mapping lives in exactly one place (no drift between the
        update sites).

        Deliberately does NOT stamp dbo-native identity when called from the fast path —
        that caller's row already carries correct identity by construction (that's how
        `read_by_qbo_identity` found it); re-stamping QboId/RealmId would be a wasted round
        trip on the steady-state path this feature exists to keep cheap. QboActive is
        handled separately: `sync_from_qbo_vendor` refreshes it itself right after this
        method returns on a fast-path hit (a QboId/RealmId-omitted `set_qbo_identity` call,
        so only the Active CASE WHEN branch fires) — not from inside this shared method,
        since the legacy callers below already stamp identity (including Active)
        themselves and doing it here too would double-write on those paths.
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
            # name is preserved the UPDATE is a pure no-op — writing it anyway would churn
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
        self._sync_addresses(qbo_vendor, vendor_id)
        return vendor

    def _raise_identity_mapping_conflict_issue(
        self,
        *,
        qbo_vendor: QboVendor,
        dbo_vendor_id: int,
        local_side_mapping: Optional[VendorVendor],
        qbo_side_mapping: Optional[VendorVendor],
    ) -> None:
        """
        Record a dbo-identity <-> mapping-table split found by run_identity_fastpath's
        pre-write check (U-290). Distinct from `_raise_missing_vendor_issue` (a
        bound-row-read-empty detection) — this is a post-hoc drift between two already-
        established identity sources, most plausibly left behind by an identity "theft"
        event (SetVendorQboIdentity's theft-clear UPDATE clears the losing row's
        QboId/RealmId but does not touch the mapping table). Covers all three shapes in
        ONE issue: qbo-side only, local-side only, or both (the "two-row crossed" case) —
        never silently dropping either side's blocker. Mirrors
        VendorCreditBillCreditConnector._raise_identity_mapping_conflict_issue (U-278).
        """
        parts = [
            f"VendorVendor identity conflict. dbo.Vendor {dbo_vendor_id} carries native "
            f"QBO identity for QboVendor {qbo_vendor.id} (QboId={qbo_vendor.qbo_id}, "
            f"RealmId={qbo_vendor.realm_id})."
        ]
        if qbo_side_mapping:
            parts.append(
                f"qbo-side: the mapping table still binds that same QboVendor to a "
                f"DIFFERENT Vendor {qbo_side_mapping.vendor_id} (mapping "
                f"{qbo_side_mapping.id}) — Bill/Purchase/VendorCredit pull, Bill's "
                f"outbound push, and the expense-coding cockpit all resolve vendor refs "
                f"through this mapping table and will keep resolving to Vendor "
                f"{qbo_side_mapping.vendor_id}, not {dbo_vendor_id}, until repointed."
            )
        if local_side_mapping:
            parts.append(
                f"local-side: Vendor {dbo_vendor_id}'s own mapping row (mapping "
                f"{local_side_mapping.id}) still binds it to a DIFFERENT QboVendor "
                f"{local_side_mapping.qbo_vendor_id}."
            )
        parts.append("Not auto-repointed — investigate which side is correct.")
        qbo_id, realm_id = _qbo_vendor_ref(qbo_vendor)
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="vendor_identity_conflict",
            entity_type="Vendor",
            entity_public_id=None,
            qbo_id=qbo_id,
            realm_id=realm_id,
            details=" ".join(parts),
        )

    def _raise_duplicate_qbo_vendor_issue(
        self,
        *,
        qbo_vendor: QboVendor,
        local_vendor: Vendor,
        existing_mapping: VendorVendor,
    ) -> None:
        """
        Record a duplicate-vendor detection on qbo.ReconciliationIssue.

        Triggered when a fresh QboVendor pull finds an existing local Vendor by exact
        name match but that Vendor is already bound to a different QboVendor. Treated as
        critical because every subsequent sync will re-detect it until resolved upstream
        in QBO.
        """
        details = (
            f"Duplicate QBO vendor detected. QboVendor {qbo_vendor.id} "
            f"(QboId={qbo_vendor.qbo_id}, DisplayName='{qbo_vendor.display_name}') "
            f"name-matches local Vendor {local_vendor.id} which is already bound to "
            f"QboVendor {existing_mapping.qbo_vendor_id}. Resolve by merging or "
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

    def _raise_blank_display_name_issue(self, *, qbo_vendor: QboVendor) -> None:
        """
        Record a blank-DisplayName detection on qbo.ReconciliationIssue.

        Triggered when a fresh QboVendor pull has no local mapping and QBO supplied
        a blank or whitespace-only DisplayName, so the connector cannot create or
        adopt a local Vendor. Treated as critical because the row will re-fail every
        sync until a DisplayName is set in QBO.
        """
        details = (
            f"Blank QBO vendor DisplayName. QboVendor {qbo_vendor.id} "
            f"(QboId={qbo_vendor.qbo_id}) has a blank or whitespace-only DisplayName "
            f"and no local VendorVendor mapping; cannot create or adopt a Vendor without "
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

    def _raise_missing_vendor_issue(self, *, qbo_vendor: QboVendor, mapping: VendorVendor) -> None:
        """
        Record an orphaned-mapping detection on qbo.ReconciliationIssue.

        Triggered when a VendorVendor mapping exists but its bound Vendor is missing AND
        no local Vendor can be resolved by name to repoint it to. We deliberately do NOT
        delete the mapping or create a Vendor here; the row is left intact for a human to
        resolve / the next tick to heal.
        """
        details = (
            f"Orphaned VendorVendor mapping. Mapping {mapping.id} (QboVendor "
            f"{qbo_vendor.id}, QboId={qbo_vendor.qbo_id}, DisplayName="
            f"'{qbo_vendor.display_name}') points at Vendor {mapping.vendor_id} which no "
            f"longer reads, and no local Vendor name-matches to repoint it. Mapping preserved; "
            f"no Vendor created. A soft-deleted vendor is the deterministic cause — restore "
            f"it or repoint the mapping by hand."
        )
        qbo_id, realm_id = _qbo_vendor_ref(qbo_vendor)
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="orphaned_vendor_vendor_mapping",
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

    def create_mapping(
        self,
        vendor_id: int,
        qbo_vendor_id: int,
        *,
        qbo_id: Optional[str],
        realm_id: Optional[str],
        active: Optional[bool] = None,
        prefetched_by_vendor=_PREFETCH_UNSET,
        prefetched_by_qbo_vendor=_PREFETCH_UNSET,
    ) -> VendorVendor:
        """
        Create a mapping between Vendor and QboVendor.
        
        Args:
            vendor_id: Database ID of Vendor record
            qbo_vendor_id: Database ID of QboVendor record
        
        Returns:
            VendorVendor: The created mapping record
        
        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints
        if prefetched_by_vendor is _PREFETCH_UNSET:
            existing_by_vendor = self.mapping_repo.read_by_vendor_id(vendor_id)
        else:
            existing_by_vendor = prefetched_by_vendor
        if existing_by_vendor:
            raise ValueError(
                f"Vendor {vendor_id} is already mapped to QboVendor {existing_by_vendor.qbo_vendor_id}"
            )

        if prefetched_by_qbo_vendor is _PREFETCH_UNSET:
            existing_by_qbo_vendor = self.mapping_repo.read_by_qbo_vendor_id(qbo_vendor_id)
        else:
            existing_by_qbo_vendor = prefetched_by_qbo_vendor
        if existing_by_qbo_vendor:
            raise ValueError(
                f"QboVendor {qbo_vendor_id} is already mapped to Vendor {existing_by_qbo_vendor.vendor_id}"
            )
        
        # Stamp dbo-native identity FIRST — if this fails, nothing else has been
        # created yet, so the caller's existing rollback fully cleans up with no
        # orphaned mapping row.
        self.vendor_service.repo.set_qbo_identity(
            id=vendor_id,
            qbo_id=qbo_id,
            realm_id=realm_id,
            active=active,
        )
        try:
            return self.mapping_repo.create(vendor_id=vendor_id, qbo_vendor_id=qbo_vendor_id)
        except DatabaseConstraintError as e:
            if e.violation.kind != UNIQUE:
                raise
            if (
                prefetched_by_vendor is not _PREFETCH_UNSET
                and "UQ_VendorVendor_VendorId" in e.original
            ):
                existing_by_vendor = self.mapping_repo.read_by_vendor_id(vendor_id)
                raise ValueError(
                    f"Vendor {vendor_id} is already mapped to QboVendor "
                    f"{existing_by_vendor.qbo_vendor_id}"
                ) from e
            if (
                prefetched_by_qbo_vendor is not _PREFETCH_UNSET
                and "UQ_VendorVendor_QboVendorId" in e.original
            ):
                existing_by_qbo_vendor = self.mapping_repo.read_by_qbo_vendor_id(qbo_vendor_id)
                raise ValueError(
                    f"QboVendor {qbo_vendor_id} is already mapped to Vendor "
                    f"{existing_by_qbo_vendor.vendor_id}"
                ) from e
            raise

    def get_mapping_by_vendor_id(self, vendor_id: int) -> Optional[VendorVendor]:
        """
        Get mapping by Vendor ID.
        """
        return self.mapping_repo.read_by_vendor_id(vendor_id)

    def get_mapping_by_qbo_vendor_id(self, qbo_vendor_id: int) -> Optional[VendorVendor]:
        """
        Get mapping by QboVendor ID.
        """
        return self.mapping_repo.read_by_qbo_vendor_id(qbo_vendor_id)
