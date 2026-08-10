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
from scripts.sync_helper import (
    WatermarkRun,
    _normalize_last_sync,
    _normalize_watermark_value,
    assert_cli_system_admin,
)
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from shared.database import with_retry
from integrations.sync.business.service import SyncService
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


def sync_qbo_to_local(
    realm_id: str,
    last_sync_time: Optional[str],
    qbo_vendor_service: QboVendorService,
    vendor_connector: VendorVendorConnector,
    outcome: SyncOutcome,
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
        sync_to_modules=False,  # We'll handle module sync separately for better control
        outcome=outcome,
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
            outcome.record_projection_error(
                vendor.id, e, label="QboVendor->Vendor", logger=logger
            )
        
        # Add delay between batches to keep connection alive
        if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(vendors):
            logger.debug(f"Processed {i + 1}/{len(vendors)} vendors, pausing...")
            time.sleep(BATCH_DELAY)
    
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
        
        provider = 'qbo'
        entity = 'vendor'
        env = 'prod'

        run = WatermarkRun(sync_service, provider, env, entity).open()
        start_time_str = _normalize_watermark_value(run.query_start)
        logger.info(f"QBO Vendor sync triggered at: {start_time_str}")

        last_sync_time = None
        if run.last_sync_time:
            last_sync_time = run.last_sync_time
            logger.info(f"Last sync time: {last_sync_time}. Fetching only updated records.")
        else:
            logger.info("No previous sync found. Performing full sync.")

        outcome = SyncOutcome()
        qbo_to_local_result = sync_qbo_to_local(
            realm_id=realm_id,
            last_sync_time=last_sync_time,
            qbo_vendor_service=qbo_vendor_service,
            vendor_connector=vendor_connector,
            outcome=outcome,
        )
        
        # Step 2: Local -> QBO push disabled in batch sync (one-way intake only).
        # The sync_local_to_qbo function is preserved for one-time pushes
        # when a record is marked Complete.
        local_to_qbo_result = {"vendors_pushed": 0}
        
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
    assert_cli_system_admin()
    result = sync_qbo_vendor()
    print(result)
