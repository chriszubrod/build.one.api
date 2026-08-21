# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.base.field_ownership import (
    preserve_human_edited_name,
    raise_if_inactive_orphaned_mapping,
    raise_if_inactive_unmapped,
)
from integrations.intuit.qbo.base.identity_fastpath import run_identity_fastpath
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.reconciliation_recorder import record_mapping_issue
from integrations.intuit.qbo.item.connector.cost_code.business.model import ItemCostCode
from integrations.intuit.qbo.item.connector.cost_code.persistence.repo import ItemCostCodeRepository
from integrations.intuit.qbo.item.business.model import QboItem
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from entities.cost_code.business.service import CostCodeService
from entities.cost_code.business.model import CostCode

logger = logging.getLogger(__name__)

_PREFETCH_UNSET = object()


class ItemCostCodeConnector:
    """
    Connector service for synchronization between QboItem and CostCode modules.
    Handles parent QBO Items (no ParentRef) mapping to CostCode.
    """

    def __init__(
        self,
        mapping_repo: Optional[ItemCostCodeRepository] = None,
        cost_code_service: Optional[CostCodeService] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        """Initialize the ItemCostCodeConnector."""
        self.mapping_repo = mapping_repo or ItemCostCodeRepository()
        self.cost_code_service = cost_code_service or CostCodeService()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()

    def sync_from_qbo_item(self, qbo_item: QboItem) -> CostCode:
        """
        Sync data from QboItem to CostCode module.
        
        This method:
        1. Parses the Item.Name to extract number and name
        2. Checks if a mapping exists
        3. Creates or updates the CostCode accordingly
        
        Args:
            qbo_item: QboItem record (must be a parent item with no ParentRef)
        
        Returns:
            CostCode: The synced CostCode record
        
        Raises:
            ValueError: If the item has a ParentRef (is not a parent item)
        """
        if qbo_item.is_child:
            raise ValueError(f"QboItem {qbo_item.id} has a ParentRef and is not a parent item")
        
        # Parse the name to get number and name
        number, name = qbo_item.parse_name()
        description = qbo_item.description

        # U-289 (Phase-4 repoint): resolve identity directly against
        # dbo.CostCode's native QboId/RealmId (U-238c) before falling back to
        # the qbo.ItemCostCode mapping-table hop below. Every CostCode synced
        # even once already carries this identity (set_qbo_identity is called
        # on both the create path and the existing-mapping update path
        # below), so this covers the steady-state case without touching
        # qbo.Item at all. Uses the shared base.identity_fastpath helper
        # (U-287) — conflict is a structural hard stop, never a fall-through
        # (that fall-through was the 2026-08-20 live-prod P0 across 6 other
        # families). Mirrors VendorCreditBillCreditConnector (U-278) /
        # TermPaymentTermConnector (U-282).
        outcome = run_identity_fastpath(
            qbo_id=qbo_item.qbo_id,
            realm_id=qbo_item.realm_id,
            external_id=qbo_item.id,
            entity_label="CostCode",
            external_label="QboItem",
            mapping_label="ItemCostCode",
            read_direct_by_qbo_identity=self.cost_code_service.read_by_qbo_identity,
            read_by_local_id=self.mapping_repo.read_by_cost_code_id,
            read_by_external_id=self.mapping_repo.read_by_qbo_item_id,
            external_id_attr="qbo_item_id",
            record_conflict_issue=lambda entity, by_local, by_external: (
                self._raise_identity_mapping_conflict_issue(
                    qbo_item=qbo_item,
                    dbo_cost_code_id=coerce_id(entity.id),
                    local_side_mapping=by_local,
                    qbo_side_mapping=by_external,
                )
            ),
            conflict_message=lambda entity: (
                f"ItemCostCode identity conflict for QboItem {qbo_item.qbo_id} "
                f"(id={qbo_item.id}): dbo.CostCode {entity.id} already carries "
                f"this identity but the mapping table disagrees. Not "
                f"auto-repointed; see the recorded reconciliation issue. "
                f"Skipping until a human resolves it."
            ),
            create_mapping=lambda local_id: self.mapping_repo.create(
                cost_code_id=local_id,
                qbo_item_id=qbo_item.id,
            ),
            apply_fields=lambda entity: self._apply_cost_code_fields_and_sync(
                entity,
                number=number,
                incoming_name=name,
                description=description,
            ),
        )
        if outcome.hit:
            return outcome.entity

        # Check for existing mapping
        mapping = self.mapping_repo.read_by_qbo_item_id(qbo_item.id)

        if mapping:
            cost_code = self.cost_code_service.read_by_id(str(mapping.cost_code_id))
            if cost_code:
                logger.info(f"Updating existing CostCode {cost_code.id} from QboItem {qbo_item.id}")
                updated = self._apply_cost_code_fields_and_sync(
                    cost_code,
                    number=number,
                    incoming_name=name,
                    description=description,
                )
                self.cost_code_service.repo.set_qbo_identity(
                    id=coerce_id(updated.id),
                    qbo_id=qbo_item.qbo_id,
                    realm_id=qbo_item.realm_id,
                )
                return updated

            # HEAL — mapping exists but the bound CostCode reads empty.
            # NEVER delete the mapping and NEVER fall through to create (audit P1-08).
            raise_if_inactive_orphaned_mapping(
                qbo_item.active,
                qbo_label="QboItem",
                qbo_id=qbo_item.id,
                target="CostCode",
            )
            replacement = self.cost_code_service.read_by_number(number)
            if replacement:
                replacement_id = coerce_id(replacement.id)
                if replacement_id != mapping.cost_code_id:
                    existing_map = self.mapping_repo.read_by_cost_code_id(replacement_id)
                    if existing_map and existing_map.qbo_item_id != qbo_item.id:
                        self._raise_duplicate_qbo_item_issue(
                            qbo_item=qbo_item,
                            local_cost_code=replacement,
                            existing_mapping=existing_map,
                        )
                        raise ValueError(
                            f"ItemCostCode mapping {mapping.id} points at missing CostCode "
                            f"{mapping.cost_code_id}; number match CostCode {replacement_id} is "
                            f"already bound to QboItem {existing_map.qbo_item_id}."
                        )
                if mapping.cost_code_id != replacement_id:
                    old_cost_code_id = mapping.cost_code_id
                    mapping.cost_code_id = replacement_id
                    self.mapping_repo.update_by_id(mapping)
                    logger.info(
                        f"Healed ItemCostCode mapping {mapping.id}: repointed QboItem "
                        f"{qbo_item.id} from missing CostCode {old_cost_code_id} to "
                        f"CostCode {replacement_id} ({number})"
                    )
                self.cost_code_service.repo.set_qbo_identity(
                    id=replacement_id,
                    qbo_id=qbo_item.qbo_id,
                    realm_id=qbo_item.realm_id,
                )
                return self._apply_cost_code_fields_and_sync(
                    replacement,
                    number=number,
                    incoming_name=name,
                    description=description,
                )

            # No replacement resolvable — record and RAISE, mutating nothing.
            self._raise_missing_cost_code_issue(qbo_item=qbo_item, mapping=mapping)
            raise ValueError(
                f"ItemCostCode mapping {mapping.id} points at missing CostCode "
                f"{mapping.cost_code_id} and no local CostCode numbered \"{number}\" could "
                f"be resolved for QboItem {qbo_item.id}; preserving mapping, skipping."
            )

        # Deactivation guard (U-219): no-mapping adopt path, before read_by_number.
        # (Heal path runs the same guard before its own read_by_number call.)
        raise_if_inactive_unmapped(
            qbo_item.active, qbo_label="QboItem", qbo_id=qbo_item.id, target="CostCode"
        )
        # No mapping. Adopt an existing unmapped local CostCode by number BEFORE creating.
        existing_by_number = self.cost_code_service.read_by_number(number)
        if existing_by_number:
            existing_id = coerce_id(existing_by_number.id)
            existing_map_for_local = self.mapping_repo.read_by_cost_code_id(existing_id)
            if existing_map_for_local:
                self._raise_duplicate_qbo_item_issue(
                    qbo_item=qbo_item,
                    local_cost_code=existing_by_number,
                    existing_mapping=existing_map_for_local,
                )
                raise ValueError(
                    f"QboItem {qbo_item.id} number-matches local CostCode {existing_id} "
                    f"which is already bound to QboItem {existing_map_for_local.qbo_item_id}."
                )
            logger.info(
                f"Binding existing local CostCode {existing_id} ({number}) "
                f"to QboItem {qbo_item.id} by number match"
            )
            # U-219: adopt-by-number deliberately assigns name RAW, bypassing preserve_human_edited_name.
            existing_by_number.name = name
            existing_by_number.description = description
            cost_code = self.cost_code_service.repo.update_by_id(existing_by_number)
            self._bind_mapping_or_raise(
                cost_code_id=existing_id,
                qbo_item_id=qbo_item.id,
                qbo_id=qbo_item.qbo_id,
                realm_id=qbo_item.realm_id,
                context=f"CostCode {existing_id} adopt",
                prefetched_by_cost_code=existing_map_for_local,
            )
            return cost_code

        # Create new CostCode + mapping.
        logger.info(f"Creating new CostCode from QboItem {qbo_item.id}: number={number}, name={name}")
        cost_code = self.cost_code_service.create(
            number=number,
            name=name,
            description=description,
        )
        cost_code_id = coerce_id(cost_code.id)
        self._bind_mapping_or_raise(
            cost_code_id=cost_code_id,
            qbo_item_id=qbo_item.id,
            qbo_id=qbo_item.qbo_id,
            realm_id=qbo_item.realm_id,
            context=f"CostCode {cost_code_id} create",
        )
        return cost_code

    def _apply_cost_code_fields_and_sync(
        self,
        cost_code: CostCode,
        *,
        number: str,
        incoming_name: Optional[str],
        description,
    ) -> CostCode:
        """
        Write QboItem-derived fields onto an existing CostCode and persist.
        Shared by the normal existing-mapping update path and the heal-in-place repoint
        path so the QboItem->CostCode field mapping lives in exactly one place.
        """
        cost_code.number = number
        cost_code.name = preserve_human_edited_name(cost_code.name, incoming_name)
        cost_code.description = description
        return self.cost_code_service.repo.update_by_id(cost_code)

    def _bind_mapping_or_raise(
        self,
        *,
        cost_code_id: int,
        qbo_item_id: int,
        qbo_id: Optional[str],
        realm_id: Optional[str],
        context: str,
        prefetched_by_cost_code=_PREFETCH_UNSET,
    ) -> None:
        try:
            self.create_mapping(
                cost_code_id=cost_code_id,
                qbo_item_id=qbo_item_id,
                qbo_id=qbo_id,
                realm_id=realm_id,
                prefetched_by_cost_code=prefetched_by_cost_code,
            )
            logger.info(f"Created mapping: CostCode {cost_code_id} <-> QboItem {qbo_item_id}")
        except ValueError as e:
            logger.error(f"Mapping creation failed after {context} (QboItem {qbo_item_id}): {e}.")
            raise

    def _raise_duplicate_qbo_item_issue(
        self,
        *,
        qbo_item: QboItem,
        local_cost_code: CostCode,
        existing_mapping: ItemCostCode,
    ) -> None:
        details = (
            f"Duplicate QBO item detected. QboItem {qbo_item.id} "
            f"(QboId={qbo_item.qbo_id}, Name='{qbo_item.name}') "
            f"number-matches local CostCode {local_cost_code.id} which is already "
            f"bound to QboItem {existing_mapping.qbo_item_id}. Resolve by merging or "
            f"renaming one of the QBO items."
        )
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="duplicate_qbo_item",
            entity_type="CostCode",
            entity_public_id=str(local_cost_code.public_id) if local_cost_code.public_id else None,
            qbo_id=str(qbo_item.qbo_id) if qbo_item.qbo_id else None,
            realm_id=qbo_item.realm_id or "",
            details=details,
        )

    def _raise_identity_mapping_conflict_issue(
        self,
        *,
        qbo_item: QboItem,
        dbo_cost_code_id: int,
        local_side_mapping: Optional[ItemCostCode],
        qbo_side_mapping: Optional[ItemCostCode],
    ) -> None:
        """
        Record a dbo-identity <-> mapping-table split found by run_identity_fastpath.
        Covers all three shapes in ONE issue: qbo-side only, local-side only, or both
        (the "two-row crossed" case) — never silently dropping either side's blocker.
        Mirrors VendorCreditBillCreditConnector._raise_identity_mapping_conflict_issue
        (U-278) / TermPaymentTermConnector (U-282).
        """
        parts = [
            f"ItemCostCode identity conflict. dbo.CostCode {dbo_cost_code_id} "
            f"carries native QBO identity for QboItem {qbo_item.id} "
            f"(QboId={qbo_item.qbo_id}, RealmId={qbo_item.realm_id})."
        ]
        if qbo_side_mapping:
            parts.append(
                f"qbo-side: the mapping table still binds that same QboItem to a "
                f"DIFFERENT CostCode {qbo_side_mapping.cost_code_id} (mapping "
                f"{qbo_side_mapping.id})."
            )
        if local_side_mapping:
            parts.append(
                f"local-side: CostCode {dbo_cost_code_id}'s own mapping row (mapping "
                f"{local_side_mapping.id}) still binds it to a DIFFERENT QboItem "
                f"{local_side_mapping.qbo_item_id}."
            )
        parts.append("Not auto-repointed — investigate which side is correct.")
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="cost_code_identity_conflict",
            entity_type="CostCode",
            entity_public_id=None,
            qbo_id=str(qbo_item.qbo_id) if qbo_item.qbo_id else None,
            realm_id=qbo_item.realm_id or "",
            details=" ".join(parts),
        )

    def _raise_missing_cost_code_issue(
        self, *, qbo_item: QboItem, mapping: ItemCostCode
    ) -> None:
        details = (
            f"Orphaned ItemCostCode mapping. Mapping {mapping.id} (QboItem "
            f"{qbo_item.id}, QboId={qbo_item.qbo_id}, Name='{qbo_item.name}') points at "
            f"CostCode {mapping.cost_code_id} which no longer reads, and no local "
            f"CostCode number-matches to repoint it. Mapping preserved; no CostCode "
            f"created."
        )
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="orphaned_item_cost_code_mapping",
            entity_type="CostCode",
            entity_public_id=None,
            qbo_id=str(qbo_item.qbo_id) if qbo_item.qbo_id else None,
            realm_id=qbo_item.realm_id or "",
            details=details,
        )

    def create_mapping(
        self,
        cost_code_id: int,
        qbo_item_id: int,
        *,
        qbo_id: Optional[str],
        realm_id: Optional[str],
        prefetched_by_cost_code=_PREFETCH_UNSET,
    ) -> ItemCostCode:
        """
        Create a mapping between CostCode and QboItem.
        
        Args:
            cost_code_id: Database ID of CostCode record
            qbo_item_id: Database ID of QboItem record
        
        Returns:
            ItemCostCode: The created mapping record
        
        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints
        if prefetched_by_cost_code is _PREFETCH_UNSET:
            existing_by_cost_code = self.mapping_repo.read_by_cost_code_id(cost_code_id)
        else:
            existing_by_cost_code = prefetched_by_cost_code
        if existing_by_cost_code:
            raise ValueError(
                f"CostCode {cost_code_id} is already mapped to QboItem {existing_by_cost_code.qbo_item_id}"
            )
        
        existing_by_qbo_item = self.mapping_repo.read_by_qbo_item_id(qbo_item_id)
        if existing_by_qbo_item:
            raise ValueError(
                f"QboItem {qbo_item_id} is already mapped to CostCode {existing_by_qbo_item.cost_code_id}"
            )
        
        self.cost_code_service.repo.set_qbo_identity(
            id=cost_code_id,
            qbo_id=qbo_id,
            realm_id=realm_id,
        )
        return self.mapping_repo.create(cost_code_id=cost_code_id, qbo_item_id=qbo_item_id)

    def get_mapping_by_cost_code_id(self, cost_code_id: int) -> Optional[ItemCostCode]:
        """
        Get mapping by CostCode ID.
        """
        return self.mapping_repo.read_by_cost_code_id(cost_code_id)

    def get_mapping_by_qbo_item_id(self, qbo_item_id: int) -> Optional[ItemCostCode]:
        """
        Get mapping by QboItem ID.
        """
        return self.mapping_repo.read_by_qbo_item_id(qbo_item_id)
