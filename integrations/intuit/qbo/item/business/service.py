# Python Standard Library Imports
import logging
from typing import List, Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.item.business.model import QboItem
from integrations.intuit.qbo.item.persistence.repo import QboItemRepository
from integrations.intuit.qbo.item.external.client import QboItemClient
from integrations.intuit.qbo.item.external.schemas import QboItem as QboItemExternalSchema
from integrations.intuit.qbo.item.connector.cost_code.business.service import ItemCostCodeConnector
from integrations.intuit.qbo.item.connector.sub_cost_code.business.service import ItemSubCostCodeConnector
from integrations.intuit.qbo.auth.business.service import QboAuthService

logger = logging.getLogger(__name__)


class QboItemService:
    """
    Service for QboItem entity business operations.
    """

    def __init__(self, repo: Optional[QboItemRepository] = None):
        """Initialize the QboItemService."""
        self.repo = repo or QboItemRepository()

    def sync_from_qbo(
        self,
        realm_id: str,
        last_updated_time: Optional[str] = None,
        sync_to_modules: bool = True
    ) -> List[QboItem]:
        """
        Fetch Items from QBO API and store locally.
        Uses upsert pattern: creates if not exists, updates if exists.
        
        Args:
            realm_id: QBO company realm ID
            last_updated_time: Optional ISO format datetime string. If provided, only fetches
                Items where Metadata.LastUpdatedTime > last_updated_time.
            sync_to_modules: If True, also sync to CostCode/SubCostCode modules
        
        Returns:
            List[QboItem]: The synced item records
        """
        # Get valid access token
        auth_service = QboAuthService()
        qbo_auth = auth_service.ensure_valid_token(realm_id=realm_id)
        
        if not qbo_auth or not qbo_auth.access_token:
            raise ValueError(f"No valid access token found for realm_id: {realm_id}")
        
        # Fetch Items from QBO API
        with QboItemClient(
            access_token=qbo_auth.access_token,
            realm_id=realm_id
        ) as client:
            qbo_items: List[QboItemExternalSchema] = client.query_all_items(
                last_updated_time=last_updated_time
            )
        
        if not qbo_items:
            logger.info(f"No Items found since {last_updated_time or 'beginning'}")
            return []
        
        logger.info(f"Retrieved {len(qbo_items)} items from QBO")
        
        # Process each item
        synced_items = []
        parent_items = []
        child_items = []
        
        for qbo_item in qbo_items:
            # Store in local database
            local_item = self._upsert_item(qbo_item, realm_id)
            synced_items.append(local_item)
            
            # Categorize for module sync
            if qbo_item.parent_ref is None:
                parent_items.append(local_item)
            else:
                child_items.append(local_item)
        
        # Sync to modules if requested
        if sync_to_modules:
            self._sync_to_cost_codes(parent_items)
            self._sync_to_sub_cost_codes(child_items)
        
        return synced_items

    def _upsert_item(self, qbo_item: QboItemExternalSchema, realm_id: str) -> QboItem:
        """
        Create or update a QboItem record.
        
        Args:
            qbo_item: QBO Item from external API
            realm_id: QBO realm ID
        
        Returns:
            QboItem: The created or updated record
        """
        # Check if item already exists
        existing = self.repo.read_by_qbo_id_and_realm_id(qbo_id=qbo_item.id, realm_id=realm_id)
        
        # Extract reference values
        parent_ref_value = qbo_item.parent_ref.value if qbo_item.parent_ref else None
        parent_ref_name = qbo_item.parent_ref.name if qbo_item.parent_ref else None
        income_account_ref_value = qbo_item.income_account_ref.value if qbo_item.income_account_ref else None
        income_account_ref_name = qbo_item.income_account_ref.name if qbo_item.income_account_ref else None
        expense_account_ref_value = qbo_item.expense_account_ref.value if qbo_item.expense_account_ref else None
        expense_account_ref_name = qbo_item.expense_account_ref.name if qbo_item.expense_account_ref else None
        
        if existing:
            # Update existing record
            logger.debug(f"Updating existing QBO item {qbo_item.id}")
            return self.repo.update_by_qbo_id(
                qbo_id=qbo_item.id,
                row_version=existing.row_version_bytes,
                sync_token=qbo_item.sync_token,
                realm_id=realm_id,
                name=qbo_item.name,
                description=qbo_item.description,
                active=qbo_item.active,
                type=qbo_item.type,
                parent_ref_value=parent_ref_value,
                parent_ref_name=parent_ref_name,
                level=qbo_item.level,
                fully_qualified_name=qbo_item.fully_qualified_name,
                sku=qbo_item.sku,
                unit_price=qbo_item.unit_price,
                purchase_cost=qbo_item.purchase_cost,
                taxable=qbo_item.taxable,
                income_account_ref_value=income_account_ref_value,
                income_account_ref_name=income_account_ref_name,
                expense_account_ref_value=expense_account_ref_value,
                expense_account_ref_name=expense_account_ref_name,
            )
        else:
            # Create new record
            logger.debug(f"Creating new QBO item {qbo_item.id}")
            return self.repo.create(
                qbo_id=qbo_item.id,
                sync_token=qbo_item.sync_token,
                realm_id=realm_id,
                name=qbo_item.name,
                description=qbo_item.description,
                active=qbo_item.active,
                type=qbo_item.type,
                parent_ref_value=parent_ref_value,
                parent_ref_name=parent_ref_name,
                level=qbo_item.level,
                fully_qualified_name=qbo_item.fully_qualified_name,
                sku=qbo_item.sku,
                unit_price=qbo_item.unit_price,
                purchase_cost=qbo_item.purchase_cost,
                taxable=qbo_item.taxable,
                income_account_ref_value=income_account_ref_value,
                income_account_ref_name=income_account_ref_name,
                expense_account_ref_value=expense_account_ref_value,
                expense_account_ref_name=expense_account_ref_name,
            )

    def _sync_to_cost_codes(self, parent_items: List[QboItem]) -> None:
        """
        Sync parent items to CostCode module.
        
        Args:
            parent_items: List of parent QboItem records (no ParentRef)
        """
        if not parent_items:
            return
        
        connector = ItemCostCodeConnector()
        
        for item in parent_items:
            try:
                cost_code = connector.sync_from_qbo_item(item)
                logger.info(f"Synced QboItem {item.id} to CostCode {cost_code.id}")
            except Exception as e:
                logger.error(f"Failed to sync QboItem {item.id} to CostCode: {e}")

    def _sync_to_sub_cost_codes(self, child_items: List[QboItem]) -> None:
        """
        Sync child items to SubCostCode module.
        
        Args:
            child_items: List of child QboItem records (with ParentRef)
        """
        if not child_items:
            return
        
        connector = ItemSubCostCodeConnector()
        
        for item in child_items:
            try:
                sub_cost_code = connector.sync_from_qbo_item(item)
                logger.info(f"Synced QboItem {item.id} to SubCostCode {sub_cost_code.id}")
            except Exception as e:
                logger.error(f"Failed to sync QboItem {item.id} to SubCostCode: {e}")

    def read_all(self) -> List[QboItem]:
        """
        Read all QboItems.
        """
        return self.repo.read_all()

    def read_by_realm_id(self, realm_id: str) -> List[QboItem]:
        """
        Read all QboItems by realm ID.
        """
        return self.repo.read_by_realm_id(realm_id)

    def read_by_qbo_id(self, qbo_id: str) -> Optional[QboItem]:
        """
        Read a QboItem by QBO ID.
        """
        return self.repo.read_by_qbo_id(qbo_id)

    def read_by_id(self, id: int) -> Optional[QboItem]:
        """
        Read a QboItem by database ID.
        """
        return self.repo.read_by_id(id)

