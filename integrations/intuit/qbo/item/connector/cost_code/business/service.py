# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.item.connector.cost_code.business.model import ItemCostCode
from integrations.intuit.qbo.item.connector.cost_code.persistence.repo import ItemCostCodeRepository
from integrations.intuit.qbo.item.business.model import QboItem
from services.cost_code.business.service import CostCodeService
from services.cost_code.business.model import CostCode

logger = logging.getLogger(__name__)


class ItemCostCodeConnector:
    """
    Connector service for synchronization between QboItem and CostCode modules.
    Handles parent QBO Items (no ParentRef) mapping to CostCode.
    """

    def __init__(
        self,
        mapping_repo: Optional[ItemCostCodeRepository] = None,
        cost_code_service: Optional[CostCodeService] = None,
    ):
        """Initialize the ItemCostCodeConnector."""
        self.mapping_repo = mapping_repo or ItemCostCodeRepository()
        self.cost_code_service = cost_code_service or CostCodeService()

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
        
        # Check for existing mapping
        mapping = self.mapping_repo.read_by_qbo_item_id(qbo_item.id)
        cost_code = None
        
        if mapping:
            # Found existing mapping - update the CostCode
            cost_code = self.cost_code_service.read_by_id(str(mapping.cost_code_id))
            if cost_code:
                logger.info(f"Updating existing CostCode {cost_code.id} from QboItem {qbo_item.id}")
                cost_code.number = number
                cost_code.name = name
                cost_code.description = description
                cost_code = self.cost_code_service.repo.update_by_id(cost_code)
            else:
                logger.warning(f"Mapping exists but CostCode {mapping.cost_code_id} not found. Creating new one.")
                # Delete the broken mapping
                self.mapping_repo.delete_by_id(mapping.id)
                mapping = None
        
        if not cost_code:
            # Check if CostCode exists by number (to prevent duplicates)
            existing_by_number = self.cost_code_service.read_by_number(number)
            if existing_by_number:
                logger.info(f"Found existing CostCode by number '{number}'. Using existing record.")
                cost_code = existing_by_number
                # Update the cost code with latest data
                cost_code.name = name
                cost_code.description = description
                cost_code = self.cost_code_service.repo.update_by_id(cost_code)
            else:
                # Create new CostCode
                logger.info(f"Creating new CostCode from QboItem {qbo_item.id}: number={number}, name={name}")
                cost_code = self.cost_code_service.create(
                    number=number,
                    name=name,
                    description=description
                )
        
        # Create mapping if needed
        if not mapping and cost_code:
            cost_code_id = int(cost_code.id) if isinstance(cost_code.id, str) else cost_code.id
            try:
                mapping = self.create_mapping(cost_code_id=cost_code_id, qbo_item_id=qbo_item.id)
                logger.info(f"Created mapping: CostCode {cost_code_id} <-> QboItem {qbo_item.id}")
            except ValueError as e:
                logger.warning(f"Could not create mapping: {e}")
        
        return cost_code

    def create_mapping(self, cost_code_id: int, qbo_item_id: int) -> ItemCostCode:
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
        existing_by_cost_code = self.mapping_repo.read_by_cost_code_id(cost_code_id)
        if existing_by_cost_code:
            raise ValueError(
                f"CostCode {cost_code_id} is already mapped to QboItem {existing_by_cost_code.qbo_item_id}"
            )
        
        existing_by_qbo_item = self.mapping_repo.read_by_qbo_item_id(qbo_item_id)
        if existing_by_qbo_item:
            raise ValueError(
                f"QboItem {qbo_item_id} is already mapped to CostCode {existing_by_qbo_item.cost_code_id}"
            )
        
        # Create mapping
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

