# Python Standard Library Imports
import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Third-party Imports

# Local Imports
from scripts.sync_helper import _normalize_last_sync, assert_cli_system_admin
from integrations.sync.business.service import SyncService
from integrations.sync.business.model import Sync
from integrations.intuit.qbo.reimburse_charge.business.service import QboReimburseChargeService
from integrations.intuit.qbo.auth.business.service import QboAuthService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def _get_or_create_sync_record(sync_service: SyncService, provider: str, env: str, entity: str) -> Sync:
    """Get or create a Sync record for the given provider/env/entity."""
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
    """Update the sync record with new last_sync_datetime."""
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


def sync_qbo_reimburse_charge(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    skip_sync_record_update: bool = False,
) -> dict:
    """
    One-way, realm-wide pull of QBO ReimburseCharges into durable staging
    (qbo.ReimburseCharge). Upsert-only — NO module / Excel / Box fan-out.

    Capturing RCs while they are still un-invoiced (and preserving the source
    pointer across the HasBeenInvoiced=true flip, KI-32) is what makes
    deterministic Tier-0 invoice-line linking possible.

    Args:
        start_date: Optional TxnDate lower bound (YYYY-MM-DD). Historical batch.
        end_date: Optional TxnDate upper bound (YYYY-MM-DD). Historical batch.
        skip_sync_record_update: If True, don't advance the watermark.
    """
    try:
        start_time = datetime.now(timezone.utc)
        start_time_str = _normalize_last_sync(start_time.isoformat())
        logger.info(f"QBO ReimburseCharge sync triggered at: {start_time_str}")

        if start_date or end_date:
            logger.info(f"Date range filter: {start_date or 'beginning'} to {end_date or 'now'}")

        # Initialize services
        sync_service = SyncService()
        rc_service = QboReimburseChargeService()
        auth_service = QboAuthService()

        # Get realm ID
        all_auths = auth_service.read_all()
        if not all_auths or len(all_auths) == 0:
            raise ValueError("No QBO authentication found. Please connect your QuickBooks account first.")
        realm_id = all_auths[0].realm_id
        logger.info(f"Using realm_id: {realm_id}")

        # Get or create Sync record
        provider = 'qbo'
        entity = 'reimburse_charge'
        env = 'prod'

        sync_record = _get_or_create_sync_record(sync_service, provider, env, entity)

        # For date-range queries, do a historical batch (no watermark filter).
        # Otherwise incremental from the last watermark.
        last_sync_time = None
        if start_date or end_date:
            logger.info("Historical batch sync mode - using date range filter instead of last sync time")
        elif sync_record and sync_record.last_sync_datetime:
            last_sync_time = sync_record.last_sync_datetime
            logger.info(f"Last sync time: {last_sync_time}. Fetching only updated records.")
        else:
            logger.info("No previous sync found. Performing full sync.")

        # Pull + upsert
        sync_result = rc_service.sync_from_qbo(
            realm_id=realm_id,
            last_updated_time=last_sync_time,
            start_date=start_date,
            end_date=end_date,
        )
        synced_count = len(sync_result["synced"])
        failed_ids = sync_result["failed_ids"]
        fetched_count = sync_result["fetched_count"]

        # Update Sync record
        end_time = datetime.now(timezone.utc)
        end_time_str = _normalize_last_sync(end_time.isoformat())

        if skip_sync_record_update:
            logger.info("Skipping sync record update (--skip-sync-update flag)")
            updated_sync = sync_record
        elif failed_ids:
            # ONE-SHOT capture (KI-32): a failed RC upsert must NOT advance the
            # watermark, or the RC's source pointer is lost forever once QBO drops
            # the reverse LinkedTxn on the invoiced flip. Hold the watermark so the
            # next tick re-pulls the window (upserts are idempotent).
            logger.warning(
                f"Watermark NOT advanced: {len(failed_ids)} reimburse charge(s) failed to "
                f"persist ({failed_ids}). Holding watermark so the window is re-pulled next run."
            )
            updated_sync = sync_record
        elif end_date:
            sync_datetime = f"{end_date}T23:59:59"
            logger.info(f"Setting sync record to end_date: {sync_datetime}")
            updated_sync = _update_sync_record(sync_service, sync_record, sync_datetime)
        else:
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
            "reimburse_charges_fetched": fetched_count,
            "reimburse_charges_synced": synced_count,
            "failed_count": len(failed_ids),
            "failed_ids": failed_ids,
        }

        logger.info(
            f"QBO ReimburseCharge sync completed. Fetched: {fetched_count}, "
            f"Synced: {synced_count}, Failed: {len(failed_ids)}"
        )

        return {
            "result": result,
            "status_code": 200,
        }

    except Exception as e:
        error_msg = f"Error syncing QBO ReimburseCharges: {str(e)}"
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
        description='Sync QBO ReimburseCharges into durable staging (qbo.ReimburseCharge)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full incremental realm-wide sync (uses last sync timestamp)
  python scripts/sync_qbo_reimburse_charge.py

  # Historical batch for a date range - sync record set to end_date
  python scripts/sync_qbo_reimburse_charge.py --start-date 2026-01-01 --end-date 2026-06-30

  # Historical batch without advancing the watermark
  python scripts/sync_qbo_reimburse_charge.py --start-date 2026-01-01 --end-date 2026-06-30 --skip-sync-update
        """
    )

    parser.add_argument(
        '--start-date',
        type=str,
        help='Start date for filtering reimburse charges by TxnDate (YYYY-MM-DD). Inclusive.',
        default=None,
    )

    parser.add_argument(
        '--end-date',
        type=str,
        help='End date for filtering reimburse charges by TxnDate (YYYY-MM-DD). Inclusive.',
        default=None,
    )

    parser.add_argument(
        '--skip-sync-update',
        action='store_true',
        help='Skip updating the sync record timestamp. Use for historical batch imports.',
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
    assert_cli_system_admin()
    args = parse_args()

    if args.start_date and not validate_date(args.start_date):
        print(f"Error: Invalid start-date format '{args.start_date}'. Use YYYY-MM-DD.")
        sys.exit(1)

    if args.end_date and not validate_date(args.end_date):
        print(f"Error: Invalid end-date format '{args.end_date}'. Use YYYY-MM-DD.")
        sys.exit(1)

    if args.start_date and args.end_date and args.start_date > args.end_date:
        print(f"Error: start-date ({args.start_date}) must be before or equal to end-date ({args.end_date}).")
        sys.exit(1)

    result = sync_qbo_reimburse_charge(
        start_date=args.start_date,
        end_date=args.end_date,
        skip_sync_record_update=args.skip_sync_update,
    )

    import json
    print(json.dumps(result, indent=2, default=str))
