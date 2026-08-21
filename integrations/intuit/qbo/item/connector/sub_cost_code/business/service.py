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
from integrations.intuit.qbo.item.connector.sub_cost_code.business.model import ItemSubCostCode
from integrations.intuit.qbo.item.connector.sub_cost_code.persistence.repo import ItemSubCostCodeRepository
from integrations.intuit.qbo.item.connector.cost_code.persistence.repo import ItemCostCodeRepository
from integrations.intuit.qbo.item.business.model import QboItem
from integrations.intuit.qbo.item.persistence.repo import QboItemRepository
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.sub_cost_code.business.model import SubCostCode

logger = logging.getLogger(__name__)

_PREFETCH_UNSET = object()


class ItemSubCostCodeConnector:
    """
    Connector service for synchronization between QboItem and SubCostCode modules.
    Handles child QBO Items (with ParentRef) mapping to SubCostCode.
    """

    def __init__(
        self,
        mapping_repo: Optional[ItemSubCostCodeRepository] = None,
        sub_cost_code_service: Optional[SubCostCodeService] = None,
        cost_code_mapping_repo: Optional[ItemCostCodeRepository] = None,
        qbo_item_repo: Optional[QboItemRepository] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        """Initialize the ItemSubCostCodeConnector."""
        self.mapping_repo = mapping_repo or ItemSubCostCodeRepository()
        self.sub_cost_code_service = sub_cost_code_service or SubCostCodeService()
        self.cost_code_mapping_repo = cost_code_mapping_repo or ItemCostCodeRepository()
        self.qbo_item_repo = qbo_item_repo or QboItemRepository()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()

    def _match_sub_cost_code_by_number_and_parent(
        self, number: str, cost_code_id: Optional[int]
    ) -> Optional[SubCostCode]:
        """Resolve a SubCostCode by number scoped to its parent CostCode."""
        if cost_code_id is None:
            return None
        siblings = self.sub_cost_code_service.repo.read_by_cost_code_id(cost_code_id)
        matches = [s for s in siblings if s.number == number]
        if not matches:
            return None
        if len(matches) > 1:
            matches.sort(key=lambda s: coerce_id(s.id))
            logger.warning(
                f"Multiple SubCostCodes numbered {number!r} under CostCode {cost_code_id}; "
                f"using lowest id {matches[0].id}"
            )
        return matches[0]

    def sync_from_qbo_item(self, qbo_item: QboItem) -> SubCostCode:
        """
        Sync data from QboItem to SubCostCode module.
        
        This method:
        1. Parses the Item.Name to extract number and name
        2. Finds the parent CostCode via ParentRef mapping
        3. Checks if a mapping exists
        4. Creates or updates the SubCostCode accordingly
        
        Args:
            qbo_item: QboItem record (must be a child item with ParentRef)
        
        Returns:
            SubCostCode: The synced SubCostCode record
        
        Raises:
            ValueError: If the item has no ParentRef (is not a child item)
            ValueError: If parent CostCode mapping not found
        """
        if qbo_item.is_parent:
            raise ValueError(f"QboItem {qbo_item.id} has no ParentRef and is not a child item")
        
        # Parse the name to get number and name
        number, name = qbo_item.parse_name()
        description = qbo_item.description
        
        # Find the parent QboItem by ParentRef.value (which is the parent's QBO ID)
        parent_qbo_id = qbo_item.parent_ref_value
        parent_qbo_item = self.qbo_item_repo.read_by_qbo_id(parent_qbo_id)
        
        if not parent_qbo_item:
            raise ValueError(f"Parent QboItem with QBO ID {parent_qbo_id} not found")
        
        # Find the CostCode mapping for the parent
        parent_cost_code_mapping = self.cost_code_mapping_repo.read_by_qbo_item_id(parent_qbo_item.id)
        
        if not parent_cost_code_mapping:
            raise ValueError(
                f"Parent QboItem {parent_qbo_item.id} is not mapped to a CostCode. "
                "Sync parent items first."
            )
        
        # SubCostCode.CostCodeId is a BIGINT referencing CostCode.Id
        # We already have the cost_code_id from the mapping
        cost_code_id = parent_cost_code_mapping.cost_code_id

        # U-289 (Phase-4 repoint): resolve identity directly against
        # dbo.SubCostCode's native QboId/RealmId (U-238c) before falling back
        # to the qbo.ItemSubCostCode mapping-table hop below. Every
        # SubCostCode synced even once already carries this identity
        # (set_qbo_identity is called on both the create path and the
        # existing-mapping update path below), so this covers the
        # steady-state case without touching qbo.Item at all. Uses the
        # shared base.identity_fastpath helper (U-287) — conflict is a
        # structural hard stop, never a fall-through (that fall-through was
        # the 2026-08-20 live-prod P0 across 6 other families). Mirrors
        # VendorCreditBillCreditConnector (U-278) / TermPaymentTermConnector
        # (U-282).
        outcome = run_identity_fastpath(
            qbo_id=qbo_item.qbo_id,
            realm_id=qbo_item.realm_id,
            external_id=qbo_item.id,
            entity_label="SubCostCode",
            external_label="QboItem",
            mapping_label="ItemSubCostCode",
            read_direct_by_qbo_identity=self.sub_cost_code_service.read_by_qbo_identity,
            read_by_local_id=self.mapping_repo.read_by_sub_cost_code_id,
            read_by_external_id=self.mapping_repo.read_by_qbo_item_id,
            external_id_attr="qbo_item_id",
            record_conflict_issue=lambda entity, by_local, by_external: (
                self._raise_identity_mapping_conflict_issue(
                    qbo_item=qbo_item,
                    dbo_sub_cost_code_id=coerce_id(entity.id),
                    local_side_mapping=by_local,
                    qbo_side_mapping=by_external,
                )
            ),
            conflict_message=lambda entity: (
                f"ItemSubCostCode identity conflict for QboItem {qbo_item.qbo_id} "
                f"(id={qbo_item.id}): dbo.SubCostCode {entity.id} already carries "
                f"this identity but the mapping table disagrees. Not "
                f"auto-repointed; see the recorded reconciliation issue. "
                f"Skipping until a human resolves it."
            ),
            create_mapping=lambda local_id: self.mapping_repo.create(
                sub_cost_code_id=local_id,
                qbo_item_id=qbo_item.id,
            ),
            apply_fields=lambda entity: self._apply_sub_cost_code_fields_and_sync(
                entity,
                number=number,
                incoming_name=name,
                description=description,
                cost_code_id=cost_code_id,
            ),
        )
        if outcome.hit:
            if outcome.entity is not None:
                # QboActive is a dbo-native mirror (U-275) that must stay
                # current every sync tick even when identity itself hasn't
                # changed (an item can be deactivated in QBO without its
                # QboId/RealmId moving) — unlike the other fast-path families,
                # SubCostCode is the only one in this batch carrying such a
                # mirror. QboId/RealmId are passed as None so the sproc's own
                # CASE WHEN guards leave them untouched (no redundant
                # re-stamp, no theft-detection re-trigger); only QboActive's
                # CASE WHEN branch fires, and only when it actually changed.
                self.sub_cost_code_service.repo.set_qbo_identity(
                    id=coerce_id(outcome.entity.id),
                    qbo_id=None,
                    realm_id=None,
                    active=qbo_item.active,
                )
            return outcome.entity

        # Check for existing mapping
        mapping = self.mapping_repo.read_by_qbo_item_id(qbo_item.id)

        if mapping:
            sub_cost_code = self.sub_cost_code_service.read_by_id(str(mapping.sub_cost_code_id))
            if sub_cost_code:
                logger.info(f"Updating existing SubCostCode {sub_cost_code.id} from QboItem {qbo_item.id}")
                updated = self._apply_sub_cost_code_fields_and_sync(
                    sub_cost_code,
                    number=number,
                    incoming_name=name,
                    description=description,
                    cost_code_id=cost_code_id,
                )
                self.sub_cost_code_service.repo.set_qbo_identity(
                    id=coerce_id(updated.id),
                    qbo_id=qbo_item.qbo_id,
                    realm_id=qbo_item.realm_id,
                    active=qbo_item.active,
                )
                return updated

            # HEAL — mapping exists but the bound SubCostCode reads empty.
            # NEVER delete the mapping and NEVER fall through to create (audit P1-08).
            raise_if_inactive_orphaned_mapping(
                qbo_item.active,
                qbo_label="QboItem",
                qbo_id=qbo_item.id,
                target="SubCostCode",
            )
            replacement = self._match_sub_cost_code_by_number_and_parent(number, cost_code_id)
            if replacement:
                replacement_id = coerce_id(replacement.id)
                if replacement_id != mapping.sub_cost_code_id:
                    existing_map = self.mapping_repo.read_by_sub_cost_code_id(replacement_id)
                    if existing_map and existing_map.qbo_item_id != qbo_item.id:
                        self._raise_duplicate_qbo_item_issue(
                            qbo_item=qbo_item,
                            local_sub_cost_code=replacement,
                            existing_mapping=existing_map,
                        )
                        raise ValueError(
                            f"ItemSubCostCode mapping {mapping.id} points at missing SubCostCode "
                            f"{mapping.sub_cost_code_id}; number match SubCostCode {replacement_id} is "
                            f"already bound to QboItem {existing_map.qbo_item_id}."
                        )
                if mapping.sub_cost_code_id != replacement_id:
                    old_sub_cost_code_id = mapping.sub_cost_code_id
                    mapping.sub_cost_code_id = replacement_id
                    self.mapping_repo.update_by_id(mapping)
                    logger.info(
                        f"Healed ItemSubCostCode mapping {mapping.id}: repointed QboItem "
                        f"{qbo_item.id} from missing SubCostCode {old_sub_cost_code_id} to "
                        f"SubCostCode {replacement_id} ({number})"
                    )
                self.sub_cost_code_service.repo.set_qbo_identity(
                    id=replacement_id,
                    qbo_id=qbo_item.qbo_id,
                    realm_id=qbo_item.realm_id,
                    active=qbo_item.active,
                )
                return self._apply_sub_cost_code_fields_and_sync(
                    replacement,
                    number=number,
                    incoming_name=name,
                    description=description,
                    cost_code_id=cost_code_id,
                )

            # No replacement resolvable — record and RAISE, mutating nothing.
            self._raise_missing_sub_cost_code_issue(qbo_item=qbo_item, mapping=mapping)
            raise ValueError(
                f"ItemSubCostCode mapping {mapping.id} points at missing SubCostCode "
                f"{mapping.sub_cost_code_id} and no local SubCostCode numbered \"{number}\" could "
                f"be resolved for QboItem {qbo_item.id}; preserving mapping, skipping."
            )

        # Deactivation guard (U-219): no-mapping adopt path, before number lookup.
        # (Heal path runs the same guard before its own number lookup.)
        raise_if_inactive_unmapped(
            qbo_item.active, qbo_label="QboItem", qbo_id=qbo_item.id, target="SubCostCode"
        )
        # No mapping. Adopt an existing unmapped local SubCostCode by number BEFORE creating.
        existing_by_number = self._match_sub_cost_code_by_number_and_parent(number, cost_code_id)
        if existing_by_number:
            existing_id = coerce_id(existing_by_number.id)
            existing_map_for_local = self.mapping_repo.read_by_sub_cost_code_id(existing_id)
            if existing_map_for_local:
                self._raise_duplicate_qbo_item_issue(
                    qbo_item=qbo_item,
                    local_sub_cost_code=existing_by_number,
                    existing_mapping=existing_map_for_local,
                )
                raise ValueError(
                    f"QboItem {qbo_item.id} number-matches local SubCostCode {existing_id} "
                    f"which is already bound to QboItem {existing_map_for_local.qbo_item_id}."
                )
            logger.info(
                f"Binding existing local SubCostCode {existing_id} ({number}) "
                f"to QboItem {qbo_item.id} by number match"
            )
            # U-219: adopt-by-number deliberately assigns name RAW, bypassing preserve_human_edited_name.
            existing_by_number.name = name
            existing_by_number.description = description
            existing_by_number.cost_code_id = cost_code_id
            sub_cost_code = self.sub_cost_code_service.repo.update_by_id(existing_by_number)
            self._bind_mapping_or_raise(
                sub_cost_code_id=existing_id,
                qbo_item_id=qbo_item.id,
                qbo_id=qbo_item.qbo_id,
                realm_id=qbo_item.realm_id,
                context=f"SubCostCode {existing_id} adopt",
                active=qbo_item.active,
                prefetched_by_sub_cost_code=existing_map_for_local,
            )
            return sub_cost_code

        # Create new SubCostCode + mapping.
        logger.info(f"Creating new SubCostCode from QboItem {qbo_item.id}: number={number}, name={name}")
        sub_cost_code = self.sub_cost_code_service.create(
            number=number,
            name=name,
            description=description,
            cost_code_id=cost_code_id,
        )
        sub_cost_code_id = coerce_id(sub_cost_code.id)
        self._bind_mapping_or_raise(
            sub_cost_code_id=sub_cost_code_id,
            qbo_item_id=qbo_item.id,
            qbo_id=qbo_item.qbo_id,
            realm_id=qbo_item.realm_id,
            context=f"SubCostCode {sub_cost_code_id} create",
            active=qbo_item.active,
        )
        return sub_cost_code

    def _apply_sub_cost_code_fields_and_sync(
        self,
        sub_cost_code: SubCostCode,
        *,
        number: str,
        incoming_name: Optional[str],
        description,
        cost_code_id: int,
    ) -> SubCostCode:
        """
        Write QboItem-derived fields onto an existing SubCostCode and persist.
        Shared by the normal existing-mapping update path and the heal-in-place repoint
        path so the QboItem->SubCostCode field mapping lives in exactly one place.
        """
        sub_cost_code.number = number
        sub_cost_code.name = preserve_human_edited_name(sub_cost_code.name, incoming_name)
        sub_cost_code.description = description
        sub_cost_code.cost_code_id = cost_code_id
        return self.sub_cost_code_service.repo.update_by_id(sub_cost_code)

    def _bind_mapping_or_raise(
        self,
        *,
        sub_cost_code_id: int,
        qbo_item_id: int,
        qbo_id: Optional[str],
        realm_id: Optional[str],
        context: str,
        active: Optional[bool] = None,
        prefetched_by_sub_cost_code=_PREFETCH_UNSET,
    ) -> None:
        try:
            self.create_mapping(
                sub_cost_code_id=sub_cost_code_id,
                qbo_item_id=qbo_item_id,
                qbo_id=qbo_id,
                realm_id=realm_id,
                active=active,
                prefetched_by_sub_cost_code=prefetched_by_sub_cost_code,
            )
            logger.info(f"Created mapping: SubCostCode {sub_cost_code_id} <-> QboItem {qbo_item_id}")
        except ValueError as e:
            logger.error(f"Mapping creation failed after {context} (QboItem {qbo_item_id}): {e}.")
            raise

    def _raise_duplicate_qbo_item_issue(
        self,
        *,
        qbo_item: QboItem,
        local_sub_cost_code: SubCostCode,
        existing_mapping: ItemSubCostCode,
    ) -> None:
        details = (
            f"Duplicate QBO item detected. QboItem {qbo_item.id} "
            f"(QboId={qbo_item.qbo_id}, Name='{qbo_item.name}') "
            f"number-matches local SubCostCode {local_sub_cost_code.id} which is already "
            f"bound to QboItem {existing_mapping.qbo_item_id}. Resolve by merging or "
            f"renaming one of the QBO items."
        )
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="duplicate_qbo_item",
            entity_type="SubCostCode",
            entity_public_id=str(local_sub_cost_code.public_id) if local_sub_cost_code.public_id else None,
            qbo_id=str(qbo_item.qbo_id) if qbo_item.qbo_id else None,
            realm_id=qbo_item.realm_id or "",
            details=details,
        )

    def _raise_identity_mapping_conflict_issue(
        self,
        *,
        qbo_item: QboItem,
        dbo_sub_cost_code_id: int,
        local_side_mapping: Optional[ItemSubCostCode],
        qbo_side_mapping: Optional[ItemSubCostCode],
    ) -> None:
        """
        Record a dbo-identity <-> mapping-table split found by run_identity_fastpath.
        Covers all three shapes in ONE issue: qbo-side only, local-side only, or both
        (the "two-row crossed" case) — never silently dropping either side's blocker.
        Mirrors VendorCreditBillCreditConnector._raise_identity_mapping_conflict_issue
        (U-278) / TermPaymentTermConnector (U-282).
        """
        parts = [
            f"ItemSubCostCode identity conflict. dbo.SubCostCode {dbo_sub_cost_code_id} "
            f"carries native QBO identity for QboItem {qbo_item.id} "
            f"(QboId={qbo_item.qbo_id}, RealmId={qbo_item.realm_id})."
        ]
        if qbo_side_mapping:
            parts.append(
                f"qbo-side: the mapping table still binds that same QboItem to a "
                f"DIFFERENT SubCostCode {qbo_side_mapping.sub_cost_code_id} (mapping "
                f"{qbo_side_mapping.id})."
            )
        if local_side_mapping:
            parts.append(
                f"local-side: SubCostCode {dbo_sub_cost_code_id}'s own mapping row "
                f"(mapping {local_side_mapping.id}) still binds it to a DIFFERENT "
                f"QboItem {local_side_mapping.qbo_item_id}."
            )
        parts.append("Not auto-repointed — investigate which side is correct.")
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="sub_cost_code_identity_conflict",
            entity_type="SubCostCode",
            entity_public_id=None,
            qbo_id=str(qbo_item.qbo_id) if qbo_item.qbo_id else None,
            realm_id=qbo_item.realm_id or "",
            details=" ".join(parts),
        )

    def _raise_missing_sub_cost_code_issue(
        self, *, qbo_item: QboItem, mapping: ItemSubCostCode
    ) -> None:
        details = (
            f"Orphaned ItemSubCostCode mapping. Mapping {mapping.id} (QboItem "
            f"{qbo_item.id}, QboId={qbo_item.qbo_id}, Name='{qbo_item.name}') points at "
            f"SubCostCode {mapping.sub_cost_code_id} which no longer reads, and no local "
            f"SubCostCode number-matches to repoint it. Mapping preserved; no SubCostCode "
            f"created."
        )
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="orphaned_item_scc_mapping",
            entity_type="SubCostCode",
            entity_public_id=None,
            qbo_id=str(qbo_item.qbo_id) if qbo_item.qbo_id else None,
            realm_id=qbo_item.realm_id or "",
            details=details,
        )

    def create_mapping(
        self,
        sub_cost_code_id: int,
        qbo_item_id: int,
        *,
        qbo_id: Optional[str],
        realm_id: Optional[str],
        active: Optional[bool] = None,
        prefetched_by_sub_cost_code=_PREFETCH_UNSET,
    ) -> ItemSubCostCode:
        """
        Create a mapping between SubCostCode and QboItem.
        
        Args:
            sub_cost_code_id: Database ID of SubCostCode record
            qbo_item_id: Database ID of QboItem record
        
        Returns:
            ItemSubCostCode: The created mapping record
        
        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints
        if prefetched_by_sub_cost_code is _PREFETCH_UNSET:
            existing_by_sub_cost_code = self.mapping_repo.read_by_sub_cost_code_id(sub_cost_code_id)
        else:
            existing_by_sub_cost_code = prefetched_by_sub_cost_code
        if existing_by_sub_cost_code:
            raise ValueError(
                f"SubCostCode {sub_cost_code_id} is already mapped to QboItem {existing_by_sub_cost_code.qbo_item_id}"
            )
        
        existing_by_qbo_item = self.mapping_repo.read_by_qbo_item_id(qbo_item_id)
        if existing_by_qbo_item:
            raise ValueError(
                f"QboItem {qbo_item_id} is already mapped to SubCostCode {existing_by_qbo_item.sub_cost_code_id}"
            )
        
        self.sub_cost_code_service.repo.set_qbo_identity(
            id=sub_cost_code_id,
            qbo_id=qbo_id,
            realm_id=realm_id,
            active=active,
        )
        return self.mapping_repo.create(sub_cost_code_id=sub_cost_code_id, qbo_item_id=qbo_item_id)

    def get_mapping_by_sub_cost_code_id(self, sub_cost_code_id: int) -> Optional[ItemSubCostCode]:
        """
        Get mapping by SubCostCode ID.
        """
        return self.mapping_repo.read_by_sub_cost_code_id(sub_cost_code_id)

    def get_mapping_by_qbo_item_id(self, qbo_item_id: int) -> Optional[ItemSubCostCode]:
        """
        Get mapping by QboItem ID.
        """
        return self.mapping_repo.read_by_qbo_item_id(qbo_item_id)
