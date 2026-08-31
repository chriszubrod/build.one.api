# Python Standard Library Imports
import logging
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Third-party Imports

# Local Imports
from scripts.sync_helper import (
    assert_cli_system_admin,
    exit_nonzero_on_sync_failure,
)
from integrations.intuit.qbo.base.locking import qbo_sync_locked_cli
from integrations.intuit.qbo.base.pacing import pace_batch
from integrations.intuit.qbo.base.watermark import (
    WatermarkRun,
    _normalize_last_sync,
    _normalize_watermark_value,
)
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from shared.database import with_retry
from integrations.sync.business.service import SyncService
from integrations.intuit.qbo.item.business.service import QboItemService
from integrations.intuit.qbo.item.business.model import QboItem
from integrations.intuit.qbo.item.connector.cost_code.business.service import ItemCostCodeConnector
from integrations.intuit.qbo.item.connector.sub_cost_code.business.service import ItemSubCostCodeConnector
from integrations.intuit.qbo.auth.business.service import QboAuthService

logger = logging.getLogger(__name__)

# Sync configuration
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


def sync_qbo_to_local(
    realm_id: str,
    last_sync_time: Optional[str],
    qbo_item_service: QboItemService,
    cost_code_connector: ItemCostCodeConnector,
    sub_cost_code_connector: ItemSubCostCodeConnector,
) -> tuple[dict, SyncOutcome]:
    """
    Sync Items from QBO API to local database and modules.
    
    Args:
        realm_id: QBO realm ID
        last_sync_time: Last sync timestamp for incremental sync
        qbo_item_service: QboItemService instance
        cost_code_connector: ItemCostCodeConnector instance
        sub_cost_code_connector: ItemSubCostCodeConnector instance
    
    Returns:
        tuple[dict, SyncOutcome]: Sync results envelope and service pull outcome
    """
    logger.info(f"Syncing Items from QBO API for realm_id: {realm_id}")
    
    # Fetch items from QBO and store locally (without auto-syncing to modules)
    outcome = qbo_item_service.sync_from_qbo(
        realm_id=realm_id,
        last_updated_time=last_sync_time,
        sync_to_modules=False  # We'll handle module sync separately for better control
    )
    items = outcome.synced
    
    if not items:
        logger.info(f"No Item updates found since {last_sync_time or 'beginning'}")
        return {
            "items_synced": 0,
            "cost_codes_synced": 0,
            "sub_cost_codes_synced": 0,
            "items": [],
        }, outcome
    
    logger.info(f"Retrieved {len(items)} items from QBO")
    
    # Separate parent and child items
    parent_items = [item for item in items if item.is_parent]
    child_items = [item for item in items if item.is_child]
    
    # Sync parent items to CostCode first (children depend on parents)
    cost_codes_synced = 0
    
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
            outcome.record_projected()
            logger.info(f"Synced QboItem {item.qbo_id} to CostCode {cost_code.id}")
        except Exception as e:
            outcome.record_projection_error(
                item.qbo_id, e, label="QboItem->CostCode", logger=logger
            )
        
        # Add delay between batches to keep connection alive
        pace_batch(i, len(parent_items), logger, "parent items")
    
    # Sync child items to SubCostCode
    sub_cost_codes_synced = 0
    
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
            outcome.record_projected()
            logger.info(f"Synced QboItem {item.qbo_id} to SubCostCode {sub_cost_code.id}")
        except Exception as e:
            outcome.record_projection_error(
                item.qbo_id, e, label="QboItem->SubCostCode", logger=logger
            )
        
        # Add delay between batches to keep connection alive
        pace_batch(i, len(child_items), logger, "child items")
    
    return {
        "items_synced": len(items),
        "cost_codes_synced": cost_codes_synced,
        "sub_cost_codes_synced": sub_cost_codes_synced,
        "items": [item.to_dict() for item in items],
    }, outcome


def sync_qbo_item() -> dict:
    """
    One-way sync for QBO Items -> CostCode/SubCostCode modules (QBO -> Local only).

    1. QBO -> Local: Fetch items modified since last sync, store locally, sync to CostCode/SubCostCode

    Note: Local -> QBO push is disabled (one-way intake only). The prior
    reverse-sync helper (`sync_local_to_qbo`) was confirmed dead code (no
    caller ever invoked it -- this entrypoint always hardcoded its result to
    zero) and was deleted (U-307c).
    """
    try:
        sync_service = SyncService()
        qbo_item_service = QboItemService()
        cost_code_connector = ItemCostCodeConnector()
        sub_cost_code_connector = ItemSubCostCodeConnector()
        auth_service = QboAuthService()
        
        # Get realm ID
        realm_id = auth_service.resolve_realm_id()
        logger.info(f"Using realm_id: {realm_id}")
        
        provider = 'qbo'
        entity = 'item'
        env = 'prod'

        run = WatermarkRun(sync_service, provider, env, entity).open()
        start_time_str = _normalize_watermark_value(run.query_start)
        logger.info(f"QBO Item sync triggered at: {start_time_str}")

        last_sync_time = None
        if run.last_sync_time:
            last_sync_time = run.last_sync_time
            logger.info(f"Last sync time: {last_sync_time}. Fetching only updated records.")
        else:
            logger.info("No previous sync found. Performing full sync.")

        qbo_to_local_result, outcome = sync_qbo_to_local(
            realm_id=realm_id,
            last_sync_time=last_sync_time,
            qbo_item_service=qbo_item_service,
            cost_code_connector=cost_code_connector,
            sub_cost_code_connector=sub_cost_code_connector,
        )
        
        # Step 2: Local -> QBO push disabled in batch sync (one-way intake only).
        # The sync_local_to_qbo function is preserved for one-time pushes
        # when a record is marked Complete.
        local_to_qbo_result = {"cost_codes_pushed": 0, "sub_cost_codes_pushed": 0}
        
        end_time = datetime.now(timezone.utc)
        end_time_str = _normalize_last_sync(end_time.isoformat())
        updated_sync = run.commit(outcome)

        result = {
            "success": True,
            "realm_id": realm_id,
            "start_time": start_time_str,
            "end_time": end_time_str,
            "sync_record": updated_sync.to_dict(),
            "watermark": {
                **outcome.summary(),
                "committed_last_sync_datetime": updated_sync.last_sync_datetime,
            },
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


@qbo_sync_locked_cli("item")
def run_locked() -> dict:
    """
    Lock-wrapped entry point for a direct CLI run (`python scripts/sync_qbo_item.py`).

    This CLI invocation is a third path onto QboItemService.sync_from_qbo,
    independent of the (already-locked) admin dispatcher — locking must live
    at this outer layer, not inside `sync_qbo_item()`, which the admin
    path also calls while already holding this same resource (see
    scripts/sync_qbo_account.py::run_locked for the full rationale).
    """
    return sync_qbo_item()


if __name__ == "__main__":
    assert_cli_system_admin()
    result = run_locked()
    print(result)
    exit_nonzero_on_sync_failure(result)

