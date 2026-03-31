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
from integrations.intuit.qbo.purchase.connector.expense.business.service import (
    PurchaseExpenseConnector,
    sync_purchase_attachments_to_expense_line_items,
)
from integrations.intuit.qbo.attachable.business.service import QboAttachableService
from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseRepository, QboPurchaseLineRepository
from integrations.intuit.qbo.purchase.external.client import QboPurchaseClient
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


def _dry_run_preview(
    realm_id: str,
    qbo_auth,
    last_sync_time: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """
    Dry-run preview: fetch purchases from QBO and report what would be synced
    without writing anything to the local database.
    """
    logger.info("[DRY RUN] Fetching purchases from QBO to preview sync (no writes will occur)...")

    with QboPurchaseClient(access_token=qbo_auth.access_token, realm_id=realm_id) as client:
        qbo_purchases = client.query_all_purchases(
            last_updated_time=last_sync_time,
            start_date=start_date,
            end_date=end_date,
        )

    logger.info(f"[DRY RUN] QBO returned {len(qbo_purchases)} purchases")

    # Check existing local QBO purchase records (read-only)
    purchase_repo = QboPurchaseRepository()
    existing = purchase_repo.read_by_realm_id(realm_id)
    existing_qbo_ids = {p.qbo_id for p in existing}

    would_create = [p for p in qbo_purchases if p.id not in existing_qbo_ids]
    would_update = [p for p in qbo_purchases if p.id in existing_qbo_ids]

    logger.info(f"[DRY RUN] QBO staging table (qbo.Purchase):")
    logger.info(f"[DRY RUN]   {len(would_create)} would be CREATED")
    logger.info(f"[DRY RUN]   {len(would_update)} would be UPDATED")
    logger.info(f"[DRY RUN] Existing local purchases: {len(existing)}")
    logger.info("[DRY RUN] No changes were made to the local database.")

    sample = [
        {"qbo_id": p.id, "doc_number": p.doc_number, "vendor": p.entity_ref.name if p.entity_ref else None, "txn_date": p.txn_date, "total": float(p.total_amt) if p.total_amt else None}
        for p in would_create[:5]
    ]

    return {
        "dry_run": True,
        "direction": "QBO → BuildOne only (read-only from QBO)",
        "qbo_records_found": len(qbo_purchases),
        "qbo_staging": {
            "would_create": len(would_create),
            "would_update": len(would_update),
        },
        "local_purchases_existing": len(existing),
        "sample_new_records": sample,
    }


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
        sync_to_modules=False,  # Module sync handled below for better control
        reconcile_deletes=True,  # Removes local records for purchases deleted in QBO (full syncs only)
    )
    
    if not purchases:
        logger.info(f"No Purchase updates found since {last_sync_time or 'beginning'}")
        return {
            "purchases_synced": 0,
            "expenses_module_synced": 0,
            "attachments_linked": 0,
            "excel_rows_synced": 0,
            "failed_count": 0,
            "failed_purchase_ids": [],
            "purchases": [],
        }
    
    logger.info(f"Retrieved {len(purchases)} purchases from QBO")
    
    # Sync purchases to Expense module
    expenses_module_synced = 0
    attachments_linked = 0
    excel_rows_synced = 0
    failed_purchases = []
    attachable_service = QboAttachableService()

    # ExpenseService + ExpenseLineItemService for Excel sync step
    from entities.expense.business.service import ExpenseService
    from entities.expense_line_item.business.service import ExpenseLineItemService
    expense_service = ExpenseService()
    expense_line_item_service = ExpenseLineItemService()

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

            # Sync attachables (download to Attachment module) and link to ExpenseLineItems
            if purchase.qbo_id:
                try:
                    qbo_attachables = attachable_service.sync_attachables_for_purchase(
                        realm_id=realm_id,
                        purchase_qbo_id=purchase.qbo_id,
                        sync_to_modules=True,
                    )
                    if qbo_attachables:
                        expense_id = int(expense.id) if isinstance(expense.id, str) else expense.id
                        linked = sync_purchase_attachments_to_expense_line_items(
                            expense_id=expense_id,
                            qbo_attachables=qbo_attachables,
                        )
                        attachments_linked += linked
                except Exception as att_e:
                    logger.warning(f"Could not sync/link attachments for Purchase {purchase.qbo_id}: {att_e}")

            # Sync to project Excel workbooks (idempotent — column Z check prevents duplicates)
            try:
                expense_id = int(expense.id) if isinstance(expense.id, str) else expense.id
                eli_list = expense_line_item_service.read_by_expense_id(expense_id=expense_id)
                # Group line items by project — only line items with a project can be synced to Excel
                line_items_by_project = {}
                for eli in eli_list:
                    if eli.project_id:
                        line_items_by_project.setdefault(eli.project_id, []).append(eli)
                for proj_id, proj_line_items in line_items_by_project.items():
                    excel_result = expense_service.sync_to_excel_workbook(
                        expense=expense,
                        line_items=proj_line_items,
                        project_id=proj_id,
                    )
                    excel_rows_synced += excel_result.get("synced_count", 0)
                    if excel_result.get("errors"):
                        for err in excel_result["errors"]:
                            logger.warning(f"Excel sync error for Expense {expense.id}, project {proj_id}: {err}")
            except Exception as excel_e:
                logger.warning(f"Could not sync Expense {expense.id} to Excel: {excel_e}")

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
        "attachments_linked": attachments_linked,
        "excel_rows_synced": excel_rows_synced,
        "failed_count": len(failed_purchases),
        "failed_purchase_ids": failed_purchases,
        "purchases": [purchase.to_dict() for purchase in purchases],
    }


def sync_qbo_purchase(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    skip_sync_record_update: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Sync QBO Purchases to Expense module.

    1. QBO -> Local: Fetch purchases modified since last sync, store locally, sync to Expense

    Args:
        start_date: Optional start date (YYYY-MM-DD) for filtering purchases by TxnDate.
        end_date: Optional end date (YYYY-MM-DD) for filtering purchases by TxnDate.
        skip_sync_record_update: If True, don't update the sync record timestamp.
        dry_run: If True, fetch from QBO and report what would be synced without writing anything.
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
            logger.info("Historical batch sync mode - using date range filter instead of last sync time")
        elif sync_record and sync_record.last_sync_datetime:
            last_sync_time = sync_record.last_sync_datetime
            logger.info(f"Last sync time: {last_sync_time}. Fetching only updated records.")
        else:
            logger.info("No previous sync found. Performing full sync.")

        # --- DRY RUN path: fetch from QBO only, no DB writes ---
        if dry_run:
            qbo_auth = auth_service.ensure_valid_token(realm_id=realm_id)
            if not qbo_auth or not qbo_auth.access_token:
                raise ValueError(f"No valid access token found for realm_id: {realm_id}")
            preview = _dry_run_preview(
                realm_id=realm_id,
                qbo_auth=qbo_auth,
                last_sync_time=last_sync_time,
                start_date=start_date,
                end_date=end_date,
            )
            end_time = datetime.now(timezone.utc)
            return {
                "result": {
                    "success": True,
                    "dry_run": True,
                    "realm_id": realm_id,
                    "start_time": start_time_str,
                    "end_time": _normalize_last_sync(end_time.isoformat()),
                    "preview": preview,
                },
                "status_code": 200,
            }

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
        
        failed_count = qbo_to_local_result.get("failed_count", 0)

        if skip_sync_record_update:
            logger.info("Skipping sync record update (--skip-sync-update flag)")
            updated_sync = sync_record
        elif failed_count > 0:
            # Do not advance the timestamp when purchases failed — they must be
            # retried on the next run.  QBO will re-return them because their
            # LastUpdatedTime is older than the preserved last_sync_time.
            logger.warning(
                f"Sync record timestamp NOT updated: {failed_count} purchase(s) failed. "
                f"They will be re-fetched and retried on the next incremental sync."
            )
            updated_sync = sync_record
        elif end_date:
            # When end_date is provided, use it as the sync record timestamp
            sync_datetime = f"{end_date}T23:59:59"
            logger.info(f"Setting sync record to end_date: {sync_datetime}")
            updated_sync = _update_sync_record(sync_service, sync_record, sync_datetime)
        else:
            # Normal incremental sync — advance to current time
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
                    f"Expenses synced: {qbo_to_local_result['expenses_module_synced']}, "
                    f"Attachments linked: {qbo_to_local_result['attachments_linked']}, "
                    f"Excel rows synced: {qbo_to_local_result.get('excel_rows_synced', 0)}")
        
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

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Fetch from QBO and report what would be synced without writing to the database.'
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
        dry_run=args.dry_run,
    )
    
    import json
    print(json.dumps(result, indent=2, default=str))
