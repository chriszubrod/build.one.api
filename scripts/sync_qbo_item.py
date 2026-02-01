# Python Standard Library Imports
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Third-party Imports

# Local Imports
from scripts.sync_helper import _normalize_last_sync
from shared.database import with_retry, is_transient_error
from integrations.sync.business.service import SyncService
from integrations.sync.business.model import Sync
from integrations.intuit.qbo.item.business.service import QboItemService
from integrations.intuit.qbo.item.business.model import QboItem
from integrations.intuit.qbo.item.connector.cost_code.business.service import ItemCostCodeConnector
from integrations.intuit.qbo.item.connector.sub_cost_code.business.service import ItemSubCostCodeConnector
from integrations.intuit.qbo.item.connector.cost_code.persistence.repo import ItemCostCodeRepository
from integrations.intuit.qbo.item.connector.sub_cost_code.persistence.repo import ItemSubCostCodeRepository
from integrations.intuit.qbo.item.persistence.repo import QboItemRepository
from integrations.intuit.qbo.auth.business.service import QboAuthService
from services.cost_code.business.service import CostCodeService
from services.sub_cost_code.business.service import SubCostCodeService

logger = logging.getLogger(__name__)

# Sync configuration
BATCH_SIZE = 10  # Process items in batches
BATCH_DELAY = 0.5  # Delay between batches (seconds)
MAX_RETRIES = 3  # Max retries for transient errors
INITIAL_RETRY_DELAY = 2.0  # Initial retry delay (seconds)


def _parse_datetime(datetime_input) -> Optional[datetime]:
    """
    Parse datetime string or object to datetime object.
    
    Args:
        datetime_input: ISO format datetime string or datetime object
    
    Returns:
        datetime: Parsed datetime object, or None if parsing fails
    """
    if not datetime_input:
        return None
    
    # If already a datetime object, return it directly
    if isinstance(datetime_input, datetime):
        return datetime_input
    
    # Convert to string if needed
    datetime_str = str(datetime_input)
    
    try:
        # Handle ISO format - remove timezone info if present
        dt_str = datetime_str.replace('Z', '').replace('+00:00', '')
        if '+' in dt_str:
            dt_str = dt_str.split('+')[0]
        
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


def _get_or_create_sync_record(sync_service: SyncService, provider: str, env: str, entity: str) -> Sync:
    """
    Get or create a Sync record for the given provider/env/entity.
    """
    all_syncs = sync_service.read_all()
    sync_record = next(
        (sync for sync in all_syncs if sync.provider == provider and sync.env == env and sync.entity == entity),
        None,
    )
    
    if not sync_record:
        sync_record = sync_service.create(
            provider=provider,
            env=env,
            entity=entity,
            last_sync_datetime=None,
        )
        logger.info(f"Created new sync record for {provider}/{env}/{entity}")
    
    return sync_record


def _update_sync_record(sync_service: SyncService, sync_record: Sync, end_time_str: str) -> Sync:
    """
    Update the sync record with new last_sync_datetime.
    """
    updated_sync = Sync(
        id=sync_record.id,
        public_id=sync_record.public_id,
        row_version=sync_record.row_version,
        created_datetime=sync_record.created_datetime,
        modified_datetime=sync_record.modified_datetime,
        provider=sync_record.provider,
        env=sync_record.env,
        entity=sync_record.entity,
        last_sync_datetime=end_time_str,
    )
    sync_service.update_by_public_id(sync_record.public_id, updated_sync)
    return updated_sync


def sync_qbo_to_local(
    realm_id: str,
    last_sync_time: Optional[str],
    qbo_item_service: QboItemService,
    cost_code_connector: ItemCostCodeConnector,
    sub_cost_code_connector: ItemSubCostCodeConnector,
) -> dict:
    """
    Sync Items from QBO API to local database and modules.
    
    Args:
        realm_id: QBO realm ID
        last_sync_time: Last sync timestamp for incremental sync
        qbo_item_service: QboItemService instance
        cost_code_connector: ItemCostCodeConnector instance
        sub_cost_code_connector: ItemSubCostCodeConnector instance
    
    Returns:
        dict: Sync results including items synced
    """
    logger.info(f"Syncing Items from QBO API for realm_id: {realm_id}")
    
    # Fetch items from QBO and store locally (without auto-syncing to modules)
    items = qbo_item_service.sync_from_qbo(
        realm_id=realm_id,
        last_updated_time=last_sync_time,
        sync_to_modules=False  # We'll handle module sync separately for better control
    )
    
    if not items:
        logger.info(f"No Item updates found since {last_sync_time or 'beginning'}")
        return {
            "items_synced": 0,
            "cost_codes_synced": 0,
            "sub_cost_codes_synced": 0,
            "items": [],
        }
    
    logger.info(f"Retrieved {len(items)} items from QBO")
    
    # Separate parent and child items
    parent_items = [item for item in items if item.is_parent]
    child_items = [item for item in items if item.is_child]
    
    # Sync parent items to CostCode first (children depend on parents)
    cost_codes_synced = 0
    failed_parents = []
    
    for i, item in enumerate(parent_items):
        try:
            # Use retry logic for transient errors
            cost_code = with_retry(
                cost_code_connector.sync_from_qbo_item,
                item,
                max_retries=MAX_RETRIES,
                initial_delay=INITIAL_RETRY_DELAY,
            )
            cost_codes_synced += 1
            logger.info(f"Synced QboItem {item.id} to CostCode {cost_code.id}")
        except Exception as e:
            logger.error(f"Failed to sync parent QboItem {item.id} to CostCode: {e}")
            failed_parents.append(item.id)
        
        # Add delay between batches to keep connection alive
        if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(parent_items):
            logger.debug(f"Processed {i + 1}/{len(parent_items)} parent items, pausing...")
            time.sleep(BATCH_DELAY)
    
    # Sync child items to SubCostCode
    sub_cost_codes_synced = 0
    failed_children = []
    
    for i, item in enumerate(child_items):
        try:
            # Use retry logic for transient errors
            sub_cost_code = with_retry(
                sub_cost_code_connector.sync_from_qbo_item,
                item,
                max_retries=MAX_RETRIES,
                initial_delay=INITIAL_RETRY_DELAY,
            )
            sub_cost_codes_synced += 1
            logger.info(f"Synced QboItem {item.id} to SubCostCode {sub_cost_code.id}")
        except Exception as e:
            logger.error(f"Failed to sync child QboItem {item.id} to SubCostCode: {e}")
            failed_children.append(item.id)
        
        # Add delay between batches to keep connection alive
        if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(child_items):
            logger.debug(f"Processed {i + 1}/{len(child_items)} child items, pausing...")
            time.sleep(BATCH_DELAY)
    
    if failed_parents:
        logger.warning(f"Failed to sync {len(failed_parents)} parent items: {failed_parents}")
    if failed_children:
        logger.warning(f"Failed to sync {len(failed_children)} child items: {failed_children}")
    
    return {
        "items_synced": len(items),
        "cost_codes_synced": cost_codes_synced,
        "sub_cost_codes_synced": sub_cost_codes_synced,
        "items": [item.to_dict() for item in items],
    }


def sync_local_to_qbo(
    realm_id: str,
    last_sync_time: Optional[str],
    qbo_item_service: QboItemService,
    cost_code_service: CostCodeService,
    sub_cost_code_service: SubCostCodeService,
    cost_code_mapping_repo: ItemCostCodeRepository,
    sub_cost_code_mapping_repo: ItemSubCostCodeRepository,
    qbo_item_repo: QboItemRepository,
) -> dict:
    """
    Sync locally modified CostCodes/SubCostCodes back to QBO.
    
    This is the reverse sync: local changes -> QBO Items.
    
    Args:
        realm_id: QBO realm ID
        last_sync_time: Last sync timestamp to detect local modifications
        Various service/repo instances
    
    Returns:
        dict: Sync results
    """
    logger.info("Checking for local CostCode/SubCostCode modifications to sync to QBO")
    
    cost_codes_pushed = 0
    sub_cost_codes_pushed = 0
    
    last_sync_dt = _parse_datetime(last_sync_time) if last_sync_time else None
    
    # Check CostCodes modified since last sync
    all_cost_codes = with_retry(
        cost_code_service.read_all,
        max_retries=MAX_RETRIES,
        initial_delay=INITIAL_RETRY_DELAY,
    )
    for i, cost_code in enumerate(all_cost_codes):
        cost_code_modified = _parse_datetime(cost_code.modified_datetime)
        
        # Skip if not modified since last sync
        if last_sync_dt and cost_code_modified and cost_code_modified <= last_sync_dt:
            continue
        
        # Find mapping to QboItem
        cost_code_id = int(cost_code.id) if isinstance(cost_code.id, str) else cost_code.id
        try:
            mapping = with_retry(
                cost_code_mapping_repo.read_by_cost_code_id,
                cost_code_id,
                max_retries=MAX_RETRIES,
                initial_delay=INITIAL_RETRY_DELAY,
            )
        except Exception as e:
            logger.error(f"Failed to read mapping for CostCode {cost_code.id}: {e}")
            continue
        
        if not mapping:
            logger.debug(f"CostCode {cost_code.id} has no QboItem mapping - skipping reverse sync")
            continue
        
        # Get the QboItem
        try:
            qbo_item = with_retry(
                qbo_item_repo.read_by_id,
                mapping.qbo_item_id,
                max_retries=MAX_RETRIES,
                initial_delay=INITIAL_RETRY_DELAY,
            )
        except Exception as e:
            logger.error(f"Failed to read QboItem {mapping.qbo_item_id}: {e}")
            continue
            
        if not qbo_item:
            logger.warning(f"QboItem {mapping.qbo_item_id} not found for CostCode {cost_code.id}")
            continue
        
        # Compare modification times
        qbo_item_modified = _parse_datetime(qbo_item.modified_datetime)
        
        if cost_code_modified and qbo_item_modified and cost_code_modified > qbo_item_modified:
            # CostCode is newer - need to update QboItem
            logger.info(f"CostCode {cost_code.id} is newer than QboItem {qbo_item.id}. Updating QboItem.")
            
            # Build new name from number and name (space-separated format)
            new_name = f"{cost_code.number} {cost_code.name}" if cost_code.number != cost_code.name else cost_code.name
            
            try:
                # Update local QboItem record
                qbo_item_repo.update_by_qbo_id(
                    qbo_id=qbo_item.qbo_id,
                    row_version=qbo_item.row_version_bytes,
                    sync_token=qbo_item.sync_token,
                    realm_id=qbo_item.realm_id,
                    name=new_name,
                    description=cost_code.description,
                    active=qbo_item.active,
                    type=qbo_item.type,
                    parent_ref_value=qbo_item.parent_ref_value,
                    parent_ref_name=qbo_item.parent_ref_name,
                    level=qbo_item.level,
                    fully_qualified_name=qbo_item.fully_qualified_name,
                    sku=qbo_item.sku,
                    unit_price=qbo_item.unit_price,
                    purchase_cost=qbo_item.purchase_cost,
                    taxable=qbo_item.taxable,
                    income_account_ref_value=qbo_item.income_account_ref_value,
                    income_account_ref_name=qbo_item.income_account_ref_name,
                    expense_account_ref_value=qbo_item.expense_account_ref_value,
                    expense_account_ref_name=qbo_item.expense_account_ref_name,
                )
                cost_codes_pushed += 1
                logger.info(f"Updated QboItem {qbo_item.id} from CostCode {cost_code.id}")
                
                # Note: Pushing to QBO API would require Item update API call
                # QBO Item API supports updates via POST, but we're only updating local for now
                # TODO: Add QBO API push when needed
                
            except Exception as e:
                logger.error(f"Failed to update QboItem {qbo_item.id} from CostCode {cost_code.id}: {e}")
    
    # Check SubCostCodes modified since last sync
    all_sub_cost_codes = with_retry(
        sub_cost_code_service.read_all,
        max_retries=MAX_RETRIES,
        initial_delay=INITIAL_RETRY_DELAY,
    )
    for i, sub_cost_code in enumerate(all_sub_cost_codes):
        sub_cost_code_modified = _parse_datetime(sub_cost_code.modified_datetime)
        
        # Skip if not modified since last sync
        if last_sync_dt and sub_cost_code_modified and sub_cost_code_modified <= last_sync_dt:
            continue
        
        # Find mapping to QboItem
        sub_cost_code_id = int(sub_cost_code.id) if isinstance(sub_cost_code.id, str) else sub_cost_code.id
        try:
            mapping = with_retry(
                sub_cost_code_mapping_repo.read_by_sub_cost_code_id,
                sub_cost_code_id,
                max_retries=MAX_RETRIES,
                initial_delay=INITIAL_RETRY_DELAY,
            )
        except Exception as e:
            logger.error(f"Failed to read mapping for SubCostCode {sub_cost_code.id}: {e}")
            continue
        
        if not mapping:
            logger.debug(f"SubCostCode {sub_cost_code.id} has no QboItem mapping - skipping reverse sync")
            continue
        
        # Get the QboItem
        try:
            qbo_item = with_retry(
                qbo_item_repo.read_by_id,
                mapping.qbo_item_id,
                max_retries=MAX_RETRIES,
                initial_delay=INITIAL_RETRY_DELAY,
            )
        except Exception as e:
            logger.error(f"Failed to read QboItem {mapping.qbo_item_id}: {e}")
            continue
            
        if not qbo_item:
            logger.warning(f"QboItem {mapping.qbo_item_id} not found for SubCostCode {sub_cost_code.id}")
            continue
        
        # Compare modification times
        qbo_item_modified = _parse_datetime(qbo_item.modified_datetime)
        
        if sub_cost_code_modified and qbo_item_modified and sub_cost_code_modified > qbo_item_modified:
            # SubCostCode is newer - need to update QboItem
            logger.info(f"SubCostCode {sub_cost_code.id} is newer than QboItem {qbo_item.id}. Updating QboItem.")
            
            # Build new name from number and name (space-separated format)
            new_name = f"{sub_cost_code.number} {sub_cost_code.name}" if sub_cost_code.number != sub_cost_code.name else sub_cost_code.name
            
            try:
                # Update local QboItem record
                qbo_item_repo.update_by_qbo_id(
                    qbo_id=qbo_item.qbo_id,
                    row_version=qbo_item.row_version_bytes,
                    sync_token=qbo_item.sync_token,
                    realm_id=qbo_item.realm_id,
                    name=new_name,
                    description=sub_cost_code.description,
                    active=qbo_item.active,
                    type=qbo_item.type,
                    parent_ref_value=qbo_item.parent_ref_value,
                    parent_ref_name=qbo_item.parent_ref_name,
                    level=qbo_item.level,
                    fully_qualified_name=qbo_item.fully_qualified_name,
                    sku=qbo_item.sku,
                    unit_price=qbo_item.unit_price,
                    purchase_cost=qbo_item.purchase_cost,
                    taxable=qbo_item.taxable,
                    income_account_ref_value=qbo_item.income_account_ref_value,
                    income_account_ref_name=qbo_item.income_account_ref_name,
                    expense_account_ref_value=qbo_item.expense_account_ref_value,
                    expense_account_ref_name=qbo_item.expense_account_ref_name,
                )
                sub_cost_codes_pushed += 1
                logger.info(f"Updated QboItem {qbo_item.id} from SubCostCode {sub_cost_code.id}")
                
            except Exception as e:
                logger.error(f"Failed to update QboItem {qbo_item.id} from SubCostCode {sub_cost_code.id}: {e}")
    
    return {
        "cost_codes_pushed": cost_codes_pushed,
        "sub_cost_codes_pushed": sub_cost_codes_pushed,
    }


def sync_qbo_item() -> dict:
    """
    Two-way sync for QBO Items <-> CostCode/SubCostCode modules.
    
    1. QBO -> Local: Fetch items modified since last sync, store locally, sync to CostCode/SubCostCode
    2. Local -> QBO: Check for locally modified CostCodes/SubCostCodes, sync back to QboItem
    """
    try:
        # Create start time variable
        start_time = datetime.now(timezone.utc)
        start_time_str = _normalize_last_sync(start_time.isoformat())
        logger.info(f"QBO Item sync triggered at: {start_time_str}")
        
        # Initialize services
        sync_service = SyncService()
        qbo_item_service = QboItemService()
        qbo_item_repo = QboItemRepository()
        cost_code_service = CostCodeService()
        sub_cost_code_service = SubCostCodeService()
        cost_code_connector = ItemCostCodeConnector()
        sub_cost_code_connector = ItemSubCostCodeConnector()
        cost_code_mapping_repo = ItemCostCodeRepository()
        sub_cost_code_mapping_repo = ItemSubCostCodeRepository()
        auth_service = QboAuthService()
        
        # Get realm ID
        all_auths = auth_service.read_all()
        if not all_auths or len(all_auths) == 0:
            raise ValueError("No QBO authentication found. Please connect your QuickBooks account first.")
        realm_id = all_auths[0].realm_id
        logger.info(f"Using realm_id: {realm_id}")
        
        # Get or create Sync record
        provider = 'qbo'
        entity = 'item'
        env = 'prod'
        
        sync_record = _get_or_create_sync_record(sync_service, provider, env, entity)
        
        # Get last sync time for incremental sync
        last_sync_time = None
        if sync_record and sync_record.last_sync_datetime:
            last_sync_time = sync_record.last_sync_datetime
            logger.info(f"Last sync time: {last_sync_time}. Fetching only updated records.")
        else:
            logger.info("No previous sync found. Performing full sync.")
        
        # Step 1: Sync from QBO to local
        qbo_to_local_result = sync_qbo_to_local(
            realm_id=realm_id,
            last_sync_time=last_sync_time,
            qbo_item_service=qbo_item_service,
            cost_code_connector=cost_code_connector,
            sub_cost_code_connector=sub_cost_code_connector,
        )
        
        # Step 2: Sync from local to QBO (reverse sync)
        local_to_qbo_result = sync_local_to_qbo(
            realm_id=realm_id,
            last_sync_time=last_sync_time,
            qbo_item_service=qbo_item_service,
            cost_code_service=cost_code_service,
            sub_cost_code_service=sub_cost_code_service,
            cost_code_mapping_repo=cost_code_mapping_repo,
            sub_cost_code_mapping_repo=sub_cost_code_mapping_repo,
            qbo_item_repo=qbo_item_repo,
        )
        
        # Update Sync record
        end_time = datetime.now(timezone.utc)
        end_time_str = _normalize_last_sync(end_time.isoformat())
        updated_sync = _update_sync_record(sync_service, sync_record, end_time_str)
        
        result = {
            "success": True,
            "realm_id": realm_id,
            "start_time": start_time_str,
            "end_time": end_time_str,
            "sync_record": updated_sync.to_dict(),
            "qbo_to_local": qbo_to_local_result,
            "local_to_qbo": local_to_qbo_result,
        }
        
        logger.info(f"QBO Item sync completed. Items from QBO: {qbo_to_local_result['items_synced']}, "
                    f"CostCodes synced: {qbo_to_local_result['cost_codes_synced']}, "
                    f"SubCostCodes synced: {qbo_to_local_result['sub_cost_codes_synced']}, "
                    f"CostCodes pushed: {local_to_qbo_result['cost_codes_pushed']}, "
                    f"SubCostCodes pushed: {local_to_qbo_result['sub_cost_codes_pushed']}")
        
        return {
            "result": result,
            "status_code": 200,
        }

    except Exception as e:
        error_msg = f"Error syncing QBO Items: {str(e)}"
        logger.exception(error_msg)
        return {
            "result": {
                "success": False,
                "error": error_msg,
            },
            "status_code": 500,
        }


if __name__ == "__main__":
    result = sync_qbo_item()
    print(result)

