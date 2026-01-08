# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.item.connector.sub_cost_code.business.model import ItemSubCostCode
from integrations.intuit.qbo.item.connector.sub_cost_code.persistence.repo import ItemSubCostCodeRepository
from integrations.intuit.qbo.item.connector.cost_code.persistence.repo import ItemCostCodeRepository
from integrations.intuit.qbo.item.business.model import QboItem
from integrations.intuit.qbo.item.persistence.repo import QboItemRepository
from modules.sub_cost_code.business.service import SubCostCodeService
from modules.sub_cost_code.business.model import SubCostCode

logger = logging.getLogger(__name__)


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
    ):
        """Initialize the ItemSubCostCodeConnector."""
        self.mapping_repo = mapping_repo or ItemSubCostCodeRepository()
        self.sub_cost_code_service = sub_cost_code_service or SubCostCodeService()
        self.cost_code_mapping_repo = cost_code_mapping_repo or ItemCostCodeRepository()
        self.qbo_item_repo = qbo_item_repo or QboItemRepository()

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
        
        # Check for existing mapping
        mapping = self.mapping_repo.read_by_qbo_item_id(qbo_item.id)
        sub_cost_code = None
        
        if mapping:
            # Found existing mapping - update the SubCostCode
            sub_cost_code = self.sub_cost_code_service.read_by_id(str(mapping.sub_cost_code_id))
            if sub_cost_code:
                logger.info(f"Updating existing SubCostCode {sub_cost_code.id} from QboItem {qbo_item.id}")
                sub_cost_code.number = number
                sub_cost_code.name = name
                sub_cost_code.description = description
                sub_cost_code.cost_code_id = cost_code_id
                sub_cost_code = self.sub_cost_code_service.repo.update_by_id(sub_cost_code)
            else:
                logger.warning(f"Mapping exists but SubCostCode {mapping.sub_cost_code_id} not found. Creating new one.")
                # Delete the broken mapping
                self.mapping_repo.delete_by_id(mapping.id)
                mapping = None
        
        if not sub_cost_code:
            # Check if SubCostCode exists by number (to prevent duplicates)
            existing_by_number = self.sub_cost_code_service.read_by_number(number)
            if existing_by_number:
                logger.info(f"Found existing SubCostCode by number '{number}'. Using existing record.")
                sub_cost_code = existing_by_number
                # Update the sub cost code with latest data
                sub_cost_code.name = name
                sub_cost_code.description = description
                sub_cost_code.cost_code_id = cost_code_id
                sub_cost_code = self.sub_cost_code_service.repo.update_by_id(sub_cost_code)
            else:
                # Create new SubCostCode
                logger.info(f"Creating new SubCostCode from QboItem {qbo_item.id}: number={number}, name={name}")
                sub_cost_code = self.sub_cost_code_service.create(
                    number=number,
                    name=name,
                    description=description,
                    cost_code_id=cost_code_id
                )
        
        # Create mapping if needed
        if not mapping and sub_cost_code:
            sub_cost_code_id = int(sub_cost_code.id) if isinstance(sub_cost_code.id, str) else sub_cost_code.id
            try:
                mapping = self.create_mapping(sub_cost_code_id=sub_cost_code_id, qbo_item_id=qbo_item.id)
                logger.info(f"Created mapping: SubCostCode {sub_cost_code_id} <-> QboItem {qbo_item.id}")
            except ValueError as e:
                logger.warning(f"Could not create mapping: {e}")
        
        return sub_cost_code

    def create_mapping(self, sub_cost_code_id: int, qbo_item_id: int) -> ItemSubCostCode:
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
        existing_by_sub_cost_code = self.mapping_repo.read_by_sub_cost_code_id(sub_cost_code_id)
        if existing_by_sub_cost_code:
            raise ValueError(
                f"SubCostCode {sub_cost_code_id} is already mapped to QboItem {existing_by_sub_cost_code.qbo_item_id}"
            )
        
        existing_by_qbo_item = self.mapping_repo.read_by_qbo_item_id(qbo_item_id)
        if existing_by_qbo_item:
            raise ValueError(
                f"QboItem {qbo_item_id} is already mapped to SubCostCode {existing_by_qbo_item.sub_cost_code_id}"
            )
        
        # Create mapping
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

