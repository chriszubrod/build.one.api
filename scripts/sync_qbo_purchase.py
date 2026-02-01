# Python Standard Library Imports
import argparse
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
from integrations.intuit.qbo.purchase.business.service import QboPurchaseService
from integrations.intuit.qbo.purchase.business.model import QboPurchase
from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector
from integrations.intuit.qbo.purchase.connector.expense.persistence.repo import PurchaseExpenseRepository
from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseRepository, QboPurchaseLineRepository
from integrations.intuit.qbo.auth.business.service import QboAuthService

logger = logging.getLogger(__name__)

# Configure logging for script execution
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Sync configuration
BATCH_SIZE = 10  # Process purchases in batches
BATCH_DELAY = 0.5  # Delay between batches (seconds)
MAX_RETRIES = 3  # Max retries for transient errors
INITIAL_RETRY_DELAY = 2.0  # Initial retry delay (seconds)


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
    qbo_purchase_service: QboPurchaseService,
    purchase_connector: PurchaseExpenseConnector,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """
    Sync Purchases from QBO API to local database and modules.
    
    Args:
        realm_id: QBO realm ID
        last_sync_time: Last sync timestamp for incremental sync
        qbo_purchase_service: QboPurchaseService instance
        purchase_connector: PurchaseExpenseConnector instance
        start_date: Optional start date (YYYY-MM-DD) for filtering by TxnDate
        end_date: Optional end date (YYYY-MM-DD) for filtering by TxnDate
    
    Returns:
        dict: Sync results including purchases synced
    """
    if start_date or end_date:
        logger.info(f"Syncing Purchases from QBO API for realm_id: {realm_id} (TxnDate: {start_date or 'beginning'} to {end_date or 'now'})")
    else:
        logger.info(f"Syncing Purchases from QBO API for realm_id: {realm_id}")
    
    # Fetch purchases from QBO and store locally (without auto-syncing to modules)
    purchases = qbo_purchase_service.sync_from_qbo(
        realm_id=realm_id,
        last_updated_time=last_sync_time,
        start_date=start_date,
        end_date=end_date,
        sync_to_modules=False  # We'll handle module sync separately for better control
    )
    
    if not purchases:
        logger.info(f"No Purchase updates found since {last_sync_time or 'beginning'}")
        return {
            "purchases_synced": 0,
            "expenses_module_synced": 0,
            "purchases": [],
        }
    
    logger.info(f"Retrieved {len(purchases)} purchases from QBO")
    
    # Sync purchases to Expense module
    expenses_module_synced = 0
    failed_purchases = []
    
    for i, purchase in enumerate(purchases):
        try:
            # Get purchase lines for this purchase
            purchase_lines = qbo_purchase_service.read_lines_by_qbo_purchase_id(purchase.id)
            
            # Use retry logic for transient errors
            expense = with_retry(
                purchase_connector.sync_from_qbo_purchase,
                purchase,
                purchase_lines,
                max_retries=MAX_RETRIES,
                initial_delay=INITIAL_RETRY_DELAY,
            )
            expenses_module_synced += 1
            logger.info(f"Synced QboPurchase {purchase.id} to Expense {expense.id}")
            
        except Exception as e:
            logger.error(f"Failed to sync QboPurchase {purchase.id} to Expense: {e}")
            failed_purchases.append(purchase.id)
        
        # Add delay between batches to keep connection alive
        if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(purchases):
            logger.debug(f"Processed {i + 1}/{len(purchases)} purchases, pausing...")
            time.sleep(BATCH_DELAY)
    
    if failed_purchases:
        logger.warning(f"Failed to sync {len(failed_purchases)} purchases: {failed_purchases}")
    
    return {
        "purchases_synced": len(purchases),
        "expenses_module_synced": expenses_module_synced,
        "purchases": [purchase.to_dict() for purchase in purchases],
    }


def sync_qbo_purchase(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    skip_sync_record_update: bool = False,
) -> dict:
    """
    Sync QBO Purchases to Expense module.
    
    1. QBO -> Local: Fetch purchases modified since last sync, store locally, sync to Expense
    
    Args:
        start_date: Optional start date (YYYY-MM-DD) for filtering purchases by TxnDate.
                   Use this for historical batch syncing.
        end_date: Optional end date (YYYY-MM-DD) for filtering purchases by TxnDate.
                 Use this for historical batch syncing.
        skip_sync_record_update: If True, don't update the sync record timestamp.
                                Use this when doing historical batch syncs to preserve
                                the incremental sync tracking.
    """
    try:
        # Create start time variable
        start_time = datetime.now(timezone.utc)
        start_time_str = _normalize_last_sync(start_time.isoformat())
        logger.info(f"QBO Purchase sync triggered at: {start_time_str}")
        
        if start_date or end_date:
            logger.info(f"Date range filter: {start_date or 'beginning'} to {end_date or 'now'}")
        
        # Initialize services
        sync_service = SyncService()
        qbo_purchase_service = QboPurchaseService()
        purchase_connector = PurchaseExpenseConnector()
        auth_service = QboAuthService()
        
        # Get realm ID
        all_auths = auth_service.read_all()
        if not all_auths or len(all_auths) == 0:
            raise ValueError("No QBO authentication found. Please connect your QuickBooks account first.")
        realm_id = all_auths[0].realm_id
        logger.info(f"Using realm_id: {realm_id}")
        
        # Get or create Sync record
        provider = 'qbo'
        entity = 'purchase'
        env = 'prod'
        
        sync_record = _get_or_create_sync_record(sync_service, provider, env, entity)
        
        # For date range queries, don't use last_sync_time (we're doing historical batch)
        # For regular incremental sync, use last_sync_time
        last_sync_time = None
        if start_date or end_date:
            logger.info(f"Historical batch sync mode - using date range filter instead of last sync time")
        elif sync_record and sync_record.last_sync_datetime:
            last_sync_time = sync_record.last_sync_datetime
            logger.info(f"Last sync time: {last_sync_time}. Fetching only updated records.")
        else:
            logger.info("No previous sync found. Performing full sync.")
        
        # Sync from QBO to local
        qbo_to_local_result = sync_qbo_to_local(
            realm_id=realm_id,
            last_sync_time=last_sync_time,
            qbo_purchase_service=qbo_purchase_service,
            purchase_connector=purchase_connector,
            start_date=start_date,
            end_date=end_date,
        )
        
        # Update Sync record
        end_time = datetime.now(timezone.utc)
        end_time_str = _normalize_last_sync(end_time.isoformat())
        
        if skip_sync_record_update:
            logger.info("Skipping sync record update (--skip-sync-update flag)")
            updated_sync = sync_record
        elif end_date:
            # When end_date is provided, use it as the sync record timestamp
            sync_datetime = f"{end_date}T23:59:59"
            logger.info(f"Setting sync record to end_date: {sync_datetime}")
            updated_sync = _update_sync_record(sync_service, sync_record, sync_datetime)
        else:
            # Normal incremental sync - use current time
            updated_sync = _update_sync_record(sync_service, sync_record, end_time_str)
        
        result = {
            "success": True,
            "realm_id": realm_id,
            "start_time": start_time_str,
            "end_time": end_time_str,
            "date_filter": {
                "start_date": start_date,
                "end_date": end_date,
            } if (start_date or end_date) else None,
            "sync_record": updated_sync.to_dict(),
            "qbo_to_local": qbo_to_local_result,
        }
        
        logger.info(f"QBO Purchase sync completed. Purchases from QBO: {qbo_to_local_result['purchases_synced']}, "
                    f"Expenses module synced: {qbo_to_local_result['expenses_module_synced']}")
        
        return {
            "result": result,
            "status_code": 200,
        }

    except Exception as e:
        error_msg = f"Error syncing QBO Purchases: {str(e)}"
        logger.exception(error_msg)
        return {
            "result": {
                "success": False,
                "error": error_msg,
            },
            "status_code": 500,
        }


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Sync QBO Purchases to BuildOne Expense module',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full incremental sync (uses last sync timestamp)
  python scripts/sync_qbo_purchase.py

  # Sync purchases for a specific year - sync record set to end_date for tracking
  python scripts/sync_qbo_purchase.py --start-date 2022-01-01 --end-date 2022-12-31
  python scripts/sync_qbo_purchase.py --start-date 2023-01-01 --end-date 2023-12-31
  python scripts/sync_qbo_purchase.py --start-date 2024-01-01 --end-date 2024-12-31

  # Sync all purchases from a start date to now (sync record set to current time)
  python scripts/sync_qbo_purchase.py --start-date 2024-01-01

  # Historical batch without updating sync record
  python scripts/sync_qbo_purchase.py --start-date 2022-01-01 --end-date 2022-12-31 --skip-sync-update

Note: When --end-date is provided, the sync record timestamp is set to the end_date,
allowing you to track progress through historical batch imports.
        """
    )
    
    parser.add_argument(
        '--start-date',
        type=str,
        help='Start date for filtering purchases by TxnDate (YYYY-MM-DD). Inclusive.',
        default=None
    )
    
    parser.add_argument(
        '--end-date',
        type=str,
        help='End date for filtering purchases by TxnDate (YYYY-MM-DD). Inclusive.',
        default=None
    )
    
    parser.add_argument(
        '--skip-sync-update',
        action='store_true',
        help='Skip updating the sync record timestamp. Use for historical batch imports.'
    )
    
    return parser.parse_args()


def validate_date(date_str: str) -> bool:
    """Validate date string format YYYY-MM-DD."""
    if not date_str:
        return True
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    args = parse_args()
    
    # Validate date formats
    if args.start_date and not validate_date(args.start_date):
        print(f"Error: Invalid start-date format '{args.start_date}'. Use YYYY-MM-DD.")
        sys.exit(1)
    
    if args.end_date and not validate_date(args.end_date):
        print(f"Error: Invalid end-date format '{args.end_date}'. Use YYYY-MM-DD.")
        sys.exit(1)
    
    # Validate date range
    if args.start_date and args.end_date:
        if args.start_date > args.end_date:
            print(f"Error: start-date ({args.start_date}) must be before or equal to end-date ({args.end_date}).")
            sys.exit(1)
    
    result = sync_qbo_purchase(
        start_date=args.start_date,
        end_date=args.end_date,
        skip_sync_record_update=args.skip_sync_update,
    )
    
    import json
    print(json.dumps(result, indent=2, default=str))
