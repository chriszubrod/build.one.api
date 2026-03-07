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
from integrations.intuit.qbo.vendor.business.service import QboVendorService
from integrations.intuit.qbo.vendor.business.model import QboVendor
from integrations.intuit.qbo.vendor.connector.vendor.business.service import VendorVendorConnector
from integrations.intuit.qbo.vendor.connector.vendor.persistence.repo import VendorVendorRepository
from integrations.intuit.qbo.vendor.persistence.repo import QboVendorRepository
from integrations.intuit.qbo.auth.business.service import QboAuthService

logger = logging.getLogger(__name__)

# Sync configuration
BATCH_SIZE = 10  # Process vendors in batches
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
    qbo_vendor_service: QboVendorService,
    vendor_connector: VendorVendorConnector,
) -> dict:
    """
    Sync Vendors from QBO API to local database and modules.
    
    Args:
        realm_id: QBO realm ID
        last_sync_time: Last sync timestamp for incremental sync
        qbo_vendor_service: QboVendorService instance
        vendor_connector: VendorVendorConnector instance
    
    Returns:
        dict: Sync results including vendors synced
    """
    logger.info(f"Syncing Vendors from QBO API for realm_id: {realm_id}")
    
    # Fetch vendors from QBO and store locally (without auto-syncing to modules)
    vendors = qbo_vendor_service.sync_from_qbo(
        realm_id=realm_id,
        last_updated_time=last_sync_time,
        sync_to_modules=False  # We'll handle module sync separately for better control
    )
    
    if not vendors:
        logger.info(f"No Vendor updates found since {last_sync_time or 'beginning'}")
        return {
            "vendors_synced": 0,
            "vendors_module_synced": 0,
            "vendors": [],
        }
    
    logger.info(f"Retrieved {len(vendors)} vendors from QBO")
    
    # Sync vendors to Vendor module
    vendors_module_synced = 0
    failed_vendors = []
    
    for i, vendor in enumerate(vendors):
        try:
            # Use retry logic for transient errors
            vendor_module = with_retry(
                vendor_connector.sync_from_qbo_vendor,
                vendor,
                max_retries=MAX_RETRIES,
                initial_delay=INITIAL_RETRY_DELAY,
            )
            vendors_module_synced += 1
            logger.info(f"Synced QboVendor {vendor.id} to Vendor {vendor_module.id}")
        except Exception as e:
            logger.error(f"Failed to sync QboVendor {vendor.id} to Vendor: {e}")
            failed_vendors.append(vendor.id)
        
        # Add delay between batches to keep connection alive
        if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(vendors):
            logger.debug(f"Processed {i + 1}/{len(vendors)} vendors, pausing...")
            time.sleep(BATCH_DELAY)
    
    if failed_vendors:
        logger.warning(f"Failed to sync {len(failed_vendors)} vendors: {failed_vendors}")
    
    return {
        "vendors_synced": len(vendors),
        "vendors_module_synced": vendors_module_synced,
        "vendors": [vendor.to_dict() for vendor in vendors],
    }


def sync_local_to_qbo(
    realm_id: str,
    last_sync_time: Optional[str],
    qbo_vendor_service: QboVendorService,
    vendor_mapping_repo: VendorVendorRepository,
    qbo_vendor_repo: QboVendorRepository,
) -> dict:
    """
    Sync locally modified Vendors back to QBO.
    
    This is the reverse sync: local changes -> QBO Vendors.
    
    Note: Currently, Vendor module modifications are not tracked,
    so this function is a placeholder for future implementation.
    
    Args:
        realm_id: QBO realm ID
        last_sync_time: Last sync timestamp to detect local modifications
        Various service/repo instances
    
    Returns:
        dict: Sync results
    """
    logger.info("Checking for local Vendor modifications to sync to QBO")
    
    vendors_pushed = 0
    
    # TODO: Implement reverse sync when Vendor module modification tracking is available
    # This would involve:
    # 1. Reading all Vendors modified since last_sync_time
    # 2. Finding their QboVendor mappings
    # 3. Comparing modification times
    # 4. Updating QboVendor records if local is newer
    # 5. Optionally pushing to QBO API
    
    logger.info("Reverse sync not yet implemented - Vendor module modification tracking not available")
    
    return {
        "vendors_pushed": vendors_pushed,
    }


def sync_qbo_vendor() -> dict:
    """
    One-way sync for QBO Vendors -> Vendor module (QBO -> Local only).

    1. QBO -> Local: Fetch vendors modified since last sync, store locally, sync to Vendor

    Note: Local -> QBO push is disabled in the batch sync process.
    The sync_local_to_qbo function is preserved for one-time pushes
    when a record is marked Complete.
    """
    try:
        # Create start time variable
        start_time = datetime.now(timezone.utc)
        start_time_str = _normalize_last_sync(start_time.isoformat())
        logger.info(f"QBO Vendor sync triggered at: {start_time_str}")
        
        # Initialize services
        sync_service = SyncService()
        qbo_vendor_service = QboVendorService()
        qbo_vendor_repo = QboVendorRepository()
        vendor_connector = VendorVendorConnector()
        vendor_mapping_repo = VendorVendorRepository()
        auth_service = QboAuthService()
        
        # Get realm ID
        all_auths = auth_service.read_all()
        if not all_auths or len(all_auths) == 0:
            raise ValueError("No QBO authentication found. Please connect your QuickBooks account first.")
        realm_id = all_auths[0].realm_id
        logger.info(f"Using realm_id: {realm_id}")
        
        # Get or create Sync record
        provider = 'qbo'
        entity = 'vendor'
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
            qbo_vendor_service=qbo_vendor_service,
            vendor_connector=vendor_connector,
        )
        
        # Step 2: Local -> QBO push disabled in batch sync (one-way intake only).
        # The sync_local_to_qbo function is preserved for one-time pushes
        # when a record is marked Complete.
        local_to_qbo_result = {"vendors_pushed": 0}
        
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
        
        logger.info(f"QBO Vendor sync completed. Vendors from QBO: {qbo_to_local_result['vendors_synced']}, "
                    f"Vendors module synced: {qbo_to_local_result['vendors_module_synced']}, "
                    f"Vendors pushed: {local_to_qbo_result['vendors_pushed']}")
        
        return {
            "result": result,
            "status_code": 200,
        }

    except Exception as e:
        error_msg = f"Error syncing QBO Vendors: {str(e)}"
        logger.exception(error_msg)
        return {
            "result": {
                "success": False,
                "error": error_msg,
            },
            "status_code": 500,
        }


if __name__ == "__main__":
    result = sync_qbo_vendor()
    print(result)
