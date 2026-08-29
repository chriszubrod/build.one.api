# Python Standard Library Imports
import logging
from datetime import datetime
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.base.identity_fastpath import (
    raise_concurrent_write_race,
    resolve_mapping_state,
    run_identity_fastpath,
)
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.reconciliation_recorder import (
    record_identity_mapping_conflict,
)
from integrations.intuit.qbo.physical_address.connector.business.model import PhysicalAddressAddress
from integrations.intuit.qbo.physical_address.connector.persistence.repo import PhysicalAddressAddressRepository
from integrations.intuit.qbo.physical_address.business.service import QboPhysicalAddressService
from integrations.intuit.qbo.physical_address.business.model import QboPhysicalAddress
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from entities.address.business.service import AddressService
from entities.address.business.model import Address

logger = logging.getLogger(__name__)


class PhysicalAddressAddressConnector:
    """
    Connector service for bidirectional synchronization between QboPhysicalAddress and Address modules.

    Field Mapping:
        QboPhysicalAddress.line1 <-> Address.street_one
        QboPhysicalAddress.line2 <-> Address.street_two
        QboPhysicalAddress.city <-> Address.city
        QboPhysicalAddress.country_sub_division_code <-> Address.state
        QboPhysicalAddress.postal_code <-> Address.zip
        QboPhysicalAddress.country <-> Address.country (converted to Country enum)
    """

    def __init__(
        self,
        mapping_repo: Optional[PhysicalAddressAddressRepository] = None,
        address_service: Optional[AddressService] = None,
        qbo_physical_address_service: Optional[QboPhysicalAddressService] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        """Initialize the PhysicalAddressAddressConnector."""
        self.mapping_repo = mapping_repo or PhysicalAddressAddressRepository()
        self.address_service = address_service or AddressService()
        self.qbo_physical_address_service = qbo_physical_address_service or QboPhysicalAddressService()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()

    def sync_from_qbo_to_address(self, qbo_physical_address_id: int) -> Address:
        """
        Sync data from QboPhysicalAddress to Address module.
        
        This method prevents duplicate Address records by:
        1. First checking if a valid mapping exists
        2. If mapping is broken/missing, searching for existing Address by street_one and city
        3. Only creating a new Address if no match is found
        
        Args:
            qbo_physical_address_id: Database ID of QboPhysicalAddress record
        
        Returns:
            Address: The synced Address record
        """
        # Read QboPhysicalAddress
        qbo_physical_address_repo = self.qbo_physical_address_service.repo
        qbo_physical_address = qbo_physical_address_repo.read_by_id(qbo_physical_address_id)
        
        if not qbo_physical_address:
            raise ValueError(f"QboPhysicalAddress with ID {qbo_physical_address_id} not found")
        
        # Map QBO fields to Address fields
        street_one = qbo_physical_address.line1
        street_two = qbo_physical_address.line2
        city = qbo_physical_address.city
        state = qbo_physical_address.country_sub_division_code
        zip_code = qbo_physical_address.postal_code

        # U-277 (Phase-4): resolve identity directly against dbo.Address's
        # native QboId/RealmId (U-238c) before falling back to the
        # qbo.PhysicalAddressAddress mapping-table hop below. Every Address
        # synced even once already carries this identity (this connector
        # stamps it unconditionally at the end of every sync — see below), so
        # this covers the steady-state case without touching
        # qbo.PhysicalAddress at all.
        #
        # Mirrors CustomerCustomerConnector's U-276 fast path exactly: the
        # mapping-table state is checked BEFORE any write, not after.
        def _apply_address_fields(entity: Address) -> Address:
            entity.street_one = street_one or ""
            entity.street_two = street_two or ""
            entity.city = city or ""
            entity.state = state or ""
            entity.zip = zip_code or ""
            updated = self.address_service.repo.update_by_id(entity)
            if not updated:
                # ROWVERSION race: a concurrent writer touched this exact
                # Address between the read_by_qbo_identity() lookup and this
                # UPDATE, so it affected 0 rows. Mirrors the legacy path's own
                # guard (Step 3 below) instead of leaving a bare None to blow
                # up on the next attribute access.
                logger.error(
                    f"Failed to update Address {entity.id} via fast path - "
                    f"update_by_id returned None (concurrent write race)"
                )
                raise_concurrent_write_race(entity_label="Address", entity_id=entity.id)
            return updated

        outcome = run_identity_fastpath(
            qbo_id=qbo_physical_address.qbo_id,
            realm_id=qbo_physical_address.realm_id,
            external_id=qbo_physical_address.id,
            entity_label="Address",
            external_label="QboPhysicalAddress",
            mapping_label="PhysicalAddressAddress",
            read_direct_by_qbo_identity=self.address_service.read_by_qbo_identity,
            read_by_local_id=self.mapping_repo.read_by_address_id,
            read_by_external_id=self.mapping_repo.read_by_qbo_physical_address_id,
            external_id_attr="qbo_physical_address_id",
            record_conflict_issue=lambda entity, by_local, by_external: (
                self._record_identity_mapping_conflict_issue(
                    qbo_physical_address=qbo_physical_address,
                    dbo_address_id=coerce_id(entity.id),
                    local_side_mapping=by_local,
                    qbo_side_mapping=by_external,
                )
            ),
            conflict_message=lambda entity: (
                f"PhysicalAddressAddress identity conflict for QboPhysicalAddress "
                f"{qbo_physical_address.qbo_id} (id={qbo_physical_address.id}): "
                f"dbo.Address {entity.id} already carries this identity but the "
                f"mapping table disagrees. Not auto-repointed; see the recorded "
                f"reconciliation issue. Skipping until a human resolves it."
            ),
            create_mapping=lambda local_id: self.mapping_repo.create(
                address_id=local_id,
                qbo_physical_address_id=qbo_physical_address_id,
            ),
            apply_fields=_apply_address_fields,
        )
        if outcome.hit:
            return outcome.entity

        # Step 1: Try to find Address via existing mapping
        mapping = self.mapping_repo.read_by_qbo_physical_address_id(qbo_physical_address_id)
        address = None
        needs_mapping_repair = False
        
        if mapping:
            address = self.address_service.read_by_id(str(mapping.address_id))
            if not address:
                logger.warning(f"Mapping exists but Address {mapping.address_id} not found. Will search by address fields.")
                needs_mapping_repair = True
        
        # Step 2: If no Address found via mapping, search by street_one and city to prevent duplicates
        if not address and street_one and city:
            existing_address = self.address_service.read_by_street_one_and_city(street_one=street_one, city=city)
            if existing_address:
                logger.info(f"Found existing Address by street/city match (ID: {existing_address.id}). Using existing record.")
                address = existing_address
                
                # Check if this Address is already mapped to a different QboPhysicalAddress
                existing_address_mapping = self.mapping_repo.read_by_address_id(
                    coerce_id(existing_address.id)
                )
                if existing_address_mapping and existing_address_mapping.qbo_physical_address_id != qbo_physical_address_id:
                    logger.warning(
                        f"Address {existing_address.id} is already mapped to QboPhysicalAddress {existing_address_mapping.qbo_physical_address_id}. "
                        f"Cannot remap to QboPhysicalAddress {qbo_physical_address_id}."
                    )
                    # Still update the Address data, but don't change mapping
                    needs_mapping_repair = False
                    mapping = existing_address_mapping
                else:
                    needs_mapping_repair = True
        
        # Step 3: Update existing Address or create new one
        #
        # U-287 retired the `protected_address_id` guard that used to sit here. It
        # existed because the fast path above USED to fall through on a detected
        # identity conflict, and the legacy path's by-street/city rediscovery could
        # then re-find and overwrite the very row the conflict check had just
        # protected. The fast path now hard-stops with a ValueError on conflict (never
        # falls through), so the guard was unreachable — and it only ever covered the
        # re-resolves-to-the-SAME-row case anyway, leaving open both the different-row
        # identity theft (the trailing set_qbo_identity stamp below would fire on the
        # wrong Address) and the duplicate-mint path. See base/identity_fastpath.py.
        if address:
            qbo_modified = self._parse_datetime(qbo_physical_address.modified_datetime)
            address_modified = self._parse_datetime(address.modified_datetime)
            
            # Check if Address data actually changed
            data_changed = (
                address.street_one != street_one or
                address.street_two != street_two or
                address.city != city or
                address.state != state or
                address.zip != zip_code
            )
            
            # Always update Address when syncing from QBO (QBO is source of truth)
            if qbo_modified and address_modified:
                if qbo_modified > address_modified:
                    logger.info(
                        f"QBO PhysicalAddress is newer (QBO: {qbo_modified}, Address: {address_modified}). "
                        f"Updating Address {address.id} with QBO data."
                    )
                elif address_modified > qbo_modified:
                    logger.warning(
                        f"Conflict: Address ModifiedDatetime ({address_modified}) is newer than QBO ({qbo_modified}), "
                        f"but updating Address {address.id} with QBO data (QBO sync takes precedence)."
                    )
                else:
                    if data_changed:
                        logger.debug("ModifiedDatetime matches but data differs - updating Address for consistency")
                    else:
                        logger.debug("Updating Address ModifiedDatetime to reflect sync time (data unchanged)")
            else:
                if data_changed:
                    logger.debug("Missing ModifiedDatetime(s) but data differs - updating Address")
                else:
                    logger.debug("Updating Address ModifiedDatetime to reflect sync time (data unchanged)")
            
            # Update Address (use empty string fallback for required NOT NULL fields)
            address.street_one = street_one or ""
            address.street_two = street_two or ""
            address.city = city or ""
            address.state = state or ""
            address.zip = zip_code or ""
            address_id_before_update = address.id
            address = self.address_service.repo.update_by_id(address)
            if address:
                logger.info(f"Successfully updated Address {address.id}. New ModifiedDatetime: {address.modified_datetime}")
            else:
                # Same ROWVERSION race as the fast path's guard above (U-291).
                logger.error("Failed to update Address - update_by_id returned None")
                raise_concurrent_write_race(
                    entity_label="Address",
                    entity_id=address_id_before_update,
                    path_label="legacy mapping-table path",
                )
        else:
            # No existing Address found - create new one
            logger.info(f"No existing Address found. Creating new Address from QboPhysicalAddress {qbo_physical_address_id}")
            address = self.address_service.create(
                street_one=street_one or "",
                street_two=street_two or "",
                city=city or "",
                state=state or "",
                zip=zip_code or ""
            )
            needs_mapping_repair = True
        
        # Step 4: Repair or create mapping if needed
        if needs_mapping_repair:
            address_id_int = coerce_id(address.id)
            
            # Delete old broken mapping if it exists
            if mapping and mapping.address_id != address_id_int:
                logger.info(f"Deleting broken mapping (old Address ID: {mapping.address_id})")
                self.mapping_repo.delete_by_id(mapping.id)
                mapping = None
            
            # Create new mapping if needed
            if not mapping:
                try:
                    mapping = self.create_mapping(
                        address_id_int,
                        qbo_physical_address_id,
                        qbo_id=qbo_physical_address.qbo_id,
                        realm_id=qbo_physical_address.realm_id,
                    )
                    logger.info(f"Created mapping: Address {address_id_int} <-> QboPhysicalAddress {qbo_physical_address_id}")
                except ValueError as e:
                    logger.warning(f"Could not create mapping: {e}")
        
        if (
            not needs_mapping_repair
            and mapping is not None
            and mapping.qbo_physical_address_id == qbo_physical_address_id
        ):
            # Only stamp when the resolved mapping points back at this QboPhysicalAddress.
            # Step 2's cannot-remap branch may leave `mapping` bound to a different staging row.
            self.address_service.repo.set_qbo_identity(
                id=coerce_id(address.id),
                qbo_id=qbo_physical_address.qbo_id,
                realm_id=qbo_physical_address.realm_id,
            )

        return address

    def sync_from_address_to_qbo(self, address_id: int) -> QboPhysicalAddress:
        """
        Sync data from Address module to QboPhysicalAddress.
        
        Args:
            address_id: Database ID of Address record
        
        Returns:
            QboPhysicalAddress: The synced QboPhysicalAddress record
        """
        # Read Address
        address = self.address_service.read_by_id(str(address_id))
        
        if not address:
            raise ValueError(f"Address with ID {address_id} not found")
        
        # Find mapping
        mapping = self.mapping_repo.read_by_address_id(address_id)
        
        if not mapping:
            raise ValueError(f"No mapping found for Address {address_id}. Create mapping first.")
        
        # Read QboPhysicalAddress
        qbo_physical_address_repo = self.qbo_physical_address_service.repo
        qbo_physical_address = qbo_physical_address_repo.read_by_id(mapping.qbo_physical_address_id)
        
        if not qbo_physical_address:
            raise ValueError(f"QboPhysicalAddress with ID {mapping.qbo_physical_address_id} not found")
        
        # Compare ModifiedDatetime for conflict resolution
        qbo_modified = self._parse_datetime(qbo_physical_address.modified_datetime)
        address_modified = self._parse_datetime(address.modified_datetime)
        
        if qbo_modified and address_modified:
            if address_modified > qbo_modified:
                # Address is newer - update QBO
                logger.info(
                    f"Address is newer (Address: {address_modified}, QBO: {qbo_modified}). "
                    f"Updating QboPhysicalAddress {qbo_physical_address.id} with Address data."
                )
                
                # Update local database record
                # Address.street_one -> QboPhysicalAddress.line1
                # Address.street_two -> QboPhysicalAddress.line2
                # Address.city -> QboPhysicalAddress.city
                # Address.state -> QboPhysicalAddress.country_sub_division_code
                # Address.zip -> QboPhysicalAddress.postal_code
                qbo_physical_address = qbo_physical_address_repo.update_by_id(
                    id=qbo_physical_address.id,
                    row_version=qbo_physical_address.row_version_bytes,
                    qbo_id=qbo_physical_address.qbo_id,
                    line1=address.street_one,
                    line2=address.street_two,
                    city=address.city,
                    country=qbo_physical_address.country,  # Keep existing country
                    country_sub_division_code=address.state,
                    postal_code=address.zip,
                )
                
            elif qbo_modified > address_modified:
                # QBO is newer - log conflict
                logger.warning(
                    f"Conflict detected: QBO is newer (QBO: {qbo_modified}, Address: {address_modified}). "
                    f"QboPhysicalAddress {qbo_physical_address.id} not updated. Consider syncing from QBO to Address."
                )
            else:
                # Same timestamp - no update needed
                logger.debug("ModifiedDatetime matches - no update needed")
        else:
            # One or both timestamps missing - update QBO
            logger.debug("Missing ModifiedDatetime - updating QboPhysicalAddress")
            qbo_physical_address = qbo_physical_address_repo.update_by_id(
                id=qbo_physical_address.id,
                row_version=qbo_physical_address.row_version_bytes,
                qbo_id=qbo_physical_address.qbo_id,
                line1=address.street_one,
                line2=address.street_two,
                city=address.city,
                country=qbo_physical_address.country,
                country_sub_division_code=address.state,
                postal_code=address.zip,
            )
        
        return qbo_physical_address

    def _resolve_mapping_state(self, *, address_id: int, qbo_physical_address: QboPhysicalAddress):
        """
        Read-only check of the PhysicalAddressAddress mapping table against a
        dbo-identity match, BEFORE any write happens (U-277 fast path).
        Mirrors CustomerCustomerConnector._resolve_mapping_state exactly — see
        that docstring for the full rationale. Checks BOTH directions since an
        address_id-only check would miss a stale mapping still binding this
        qbo_physical_address_id to a DIFFERENT Address.

        NOTE (U-287): no production caller — `sync_from_qbo_*` passes these same
        accessors straight to `run_identity_fastpath`, which calls the shared
        `resolve_mapping_state` itself. Retained as the per-family test seam for the
        U-276/277/278/279 suites, which call this by name. Disposition booked in TODO.md.

        Returns (state, by_address, by_qbo_physical_address) — see
        base.identity_fastpath.resolve_mapping_state, which owns the algorithm
        (U-287); this is the PhysicalAddressAddress binding of it.
        """
        return resolve_mapping_state(
            local_id=address_id,
            external_id=qbo_physical_address.id,
            read_by_local_id=self.mapping_repo.read_by_address_id,
            read_by_external_id=self.mapping_repo.read_by_qbo_physical_address_id,
            external_id_attr="qbo_physical_address_id",
        )

    def _record_identity_mapping_conflict_issue(
        self,
        *,
        qbo_physical_address: QboPhysicalAddress,
        dbo_address_id: int,
        local_side_mapping: Optional[PhysicalAddressAddress],
        qbo_side_mapping: Optional[PhysicalAddressAddress],
    ) -> None:
        record_identity_mapping_conflict(
            self.reconciliation_repo,
            drift_type="address_identity_conflict",
            entity_type="Address",
            mapping_label="PhysicalAddressAddress",
            qbo_label="QboPhysicalAddress",
            dbo_id=dbo_address_id,
            qbo_row_id=qbo_physical_address.id,
            raw_qbo_id=qbo_physical_address.qbo_id,
            raw_realm_id=qbo_physical_address.realm_id,
            realm_id=qbo_physical_address.realm_id,
            local_side_mapping=local_side_mapping,
            qbo_side_mapping=qbo_side_mapping,
            qbo_side_local_fk_attr="address_id",
            local_side_qbo_fk_attr="qbo_physical_address_id",
        )

    def create_mapping(
        self,
        address_id: int,
        qbo_physical_address_id: int,
        *,
        qbo_id: Optional[str],
        realm_id: Optional[str],
    ) -> PhysicalAddressAddress:
        """
        Create a mapping between Address and QboPhysicalAddress.
        
        Args:
            address_id: Database ID of Address record
            qbo_physical_address_id: Database ID of QboPhysicalAddress record
        
        Returns:
            PhysicalAddressAddress: The created mapping record
        
        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints
        existing_by_address = self.mapping_repo.read_by_address_id(address_id)
        if existing_by_address:
            raise ValueError(
                f"Address {address_id} is already mapped to QboPhysicalAddress {existing_by_address.qbo_physical_address_id}"
            )
        
        existing_by_qbo = self.mapping_repo.read_by_qbo_physical_address_id(qbo_physical_address_id)
        if existing_by_qbo:
            raise ValueError(
                f"QboPhysicalAddress {qbo_physical_address_id} is already mapped to Address {existing_by_qbo.address_id}"
            )
        
        self.address_service.repo.set_qbo_identity(
            id=address_id,
            qbo_id=qbo_id,
            realm_id=realm_id,
        )
        return self.mapping_repo.create(address_id=address_id, qbo_physical_address_id=qbo_physical_address_id)

    def get_mapping_by_address_id(self, address_id: int) -> Optional[PhysicalAddressAddress]:
        """
        Get mapping by Address ID.
        """
        return self.mapping_repo.read_by_address_id(address_id)

    def get_mapping_by_qbo_physical_address_id(self, qbo_physical_address_id: int) -> Optional[PhysicalAddressAddress]:
        """
        Get mapping by QboPhysicalAddress ID.
        """
        return self.mapping_repo.read_by_qbo_physical_address_id(qbo_physical_address_id)

    @staticmethod
    def _parse_datetime(datetime_str: Optional[str]) -> Optional[datetime]:
        """
        Parse datetime string to datetime object.
        """
        if not datetime_str:
            return None
        
        try:
            # Handle ISO format - remove timezone info if present
            dt_str = datetime_str.replace('Z', '').replace('+00:00', '')
            if '+' in dt_str:
                dt_str = dt_str.split('+')[0]
            if '-' in dt_str and dt_str.count('-') > 2:
                parts = dt_str.rsplit('-', 2)
                dt_str = parts[0]
            
            # Try parsing with space separator (SQL Server format)
            if ' ' in dt_str and 'T' not in dt_str:
                return datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
            # Try parsing with T separator (ISO format)
            elif 'T' in dt_str:
                dt_str = dt_str.replace('T', ' ')
                if '.' in dt_str:
                    return datetime.strptime(dt_str.split('.')[0], '%Y-%m-%d %H:%M:%S')
                else:
                    return datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
            else:
                return datetime.strptime(dt_str, '%Y-%m-%d')
        except (ValueError, AttributeError) as e:
            logger.warning(f"Failed to parse datetime '{datetime_str}': {e}")
            return None

