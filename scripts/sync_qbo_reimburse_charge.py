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
from scripts.sync_helper import (
    WatermarkRun,
    _normalize_last_sync,
    _normalize_watermark_value,
    assert_cli_system_admin,
)
from integrations.sync.business.service import SyncService
from integrations.intuit.qbo.reimburse_charge.business.service import QboReimburseChargeService
from integrations.intuit.qbo.auth.business.service import QboAuthService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


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
        sync_service = SyncService()
        rc_service = QboReimburseChargeService()
        auth_service = QboAuthService()

        provider = 'qbo'
        entity = 'reimburse_charge'
        env = 'prod'

        run = WatermarkRun(sync_service, provider, env, entity).open()
        start_time_str = _normalize_watermark_value(run.query_start)
        logger.info(f"QBO ReimburseCharge sync triggered at: {start_time_str}")

        if start_date or end_date:
            logger.info(f"Date range filter: {start_date or 'beginning'} to {end_date or 'now'}")

        # Get realm ID
        all_auths = auth_service.read_all()
        if not all_auths or len(all_auths) == 0:
            raise ValueError("No QBO authentication found. Please connect your QuickBooks account first.")
        realm_id = all_auths[0].realm_id
        logger.info(f"Using realm_id: {realm_id}")

        # For date-range queries, do a historical batch (no watermark filter).
        # Otherwise incremental from the last watermark.
        last_sync_time = None
        if start_date or end_date:
            logger.info("Historical batch sync mode - using date range filter instead of last sync time")
        elif run.last_sync_time:
            last_sync_time = run.last_sync_time
            logger.info(f"Last sync time: {last_sync_time}. Fetching only updated records.")
        else:
            logger.info("No previous sync found. Performing full sync.")

        # Pull + upsert
        outcome = rc_service.sync_from_qbo(
            realm_id=realm_id,
            last_updated_time=last_sync_time,
            start_date=start_date,
            end_date=end_date,
        )

        end_time = datetime.now(timezone.utc)
        end_time_str = _normalize_last_sync(end_time.isoformat())

        updated_sync = run.commit(outcome, end_date=end_date, skip=skip_sync_record_update)

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
            "watermark": {
                **outcome.summary(),
                "committed_last_sync_datetime": updated_sync.last_sync_datetime,
            },
            "reimburse_charges_fetched": outcome.fetched,
            "reimburse_charges_synced": outcome.synced_count,
            "failed_count": outcome.failed_count,
            "failed_ids": outcome.staging_failed_ids,
        }

        logger.info(
            f"QBO ReimburseCharge sync completed. Fetched: {outcome.fetched}, "
            f"Synced: {outcome.synced_count}, Failed: {outcome.failed_count}"
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
