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
from scripts.sync_helper import _normalize_last_sync
from integrations.sync.business.service import SyncService
from integrations.sync.business.model import Sync
from integrations.intuit.qbo.account.business.service import QboAccountService
from integrations.intuit.qbo.account.external.client import QboAccountClient
from integrations.intuit.qbo.account.persistence.repo import QboAccountRepository
from integrations.intuit.qbo.auth.business.service import QboAuthService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


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
) -> dict:
    """
    Dry-run preview: fetch accounts from QBO and report what would be synced
    without writing anything to the local database.
    """
    logger.info("[DRY RUN] Fetching accounts from QBO to preview sync (no writes will occur)...")

    with QboAccountClient(access_token=qbo_auth.access_token, realm_id=realm_id) as client:
        qbo_accounts = client.query_all_accounts(last_updated_time=last_sync_time)

    logger.info(f"[DRY RUN] QBO returned {len(qbo_accounts)} accounts")

    # Check existing local QBO account records (read-only)
    account_repo = QboAccountRepository()
    existing = account_repo.read_by_realm_id(realm_id)
    existing_qbo_ids = {a.qbo_id for a in existing}

    would_create = [a for a in qbo_accounts if a.id not in existing_qbo_ids]
    would_update = [a for a in qbo_accounts if a.id in existing_qbo_ids]
    deactivated_in_qbo = [a for a in qbo_accounts if a.active is False]

    logger.info(f"[DRY RUN] QBO staging table (qbo.Account):")
    logger.info(f"[DRY RUN]   {len(would_create)} would be CREATED")
    logger.info(f"[DRY RUN]   {len(would_update)} would be UPDATED")
    logger.info(f"[DRY RUN]   {len(deactivated_in_qbo)} are deactivated in QBO")
    logger.info("[DRY RUN] No changes were made to the local database.")

    sample = [
        {"qbo_id": a.id, "name": a.name, "acct_num": a.acct_num, "type": a.account_type, "active": a.active}
        for a in would_create[:5]
    ]

    return {
        "dry_run": True,
        "direction": "QBO → BuildOne only (read-only from QBO)",
        "qbo_records_found": len(qbo_accounts),
        "qbo_staging": {
            "would_create": len(would_create),
            "would_update": len(would_update),
        },
        "deactivated_in_qbo": len(deactivated_in_qbo),
        "local_accounts_existing": len(existing),
        "sample_new_records": sample,
    }


def sync_qbo_to_local(
    realm_id: str,
    last_sync_time: Optional[str],
    qbo_account_service: QboAccountService,
    reconcile_deletes: bool = False,
) -> dict:
    """
    Sync Accounts from QBO API to local database.

    Args:
        realm_id: QBO realm ID
        last_sync_time: Last sync timestamp for incremental sync
        qbo_account_service: QboAccountService instance
        reconcile_deletes: If True, deactivate local records not found in QBO (full sync only)

    Returns:
        dict: Sync results including accounts synced
    """
    logger.info(f"Syncing Accounts from QBO API for realm_id: {realm_id}")

    # Fetch accounts from QBO and store locally
    accounts = qbo_account_service.sync_from_qbo(
        realm_id=realm_id,
        last_updated_time=last_sync_time,
        reconcile_deletes=reconcile_deletes,
    )
    
    if not accounts:
        logger.info(f"No Account updates found since {last_sync_time or 'beginning'}")
        return {
            "accounts_synced": 0,
            "accounts": [],
        }
    
    logger.info(f"Synced {len(accounts)} accounts from QBO")
    
    return {
        "accounts_synced": len(accounts),
        "accounts": [account.to_dict() for account in accounts],
    }


def sync_qbo_account(
    skip_sync_record_update: bool = False,
    reconcile_deletes: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    One-way sync for QBO Accounts -> local database.

    Fetches accounts from QBO API and stores them locally.

    Args:
        skip_sync_record_update: If True, don't update the sync record timestamp.
        reconcile_deletes: If True, deactivate local records not found in QBO (full sync only).
        dry_run: If True, fetch from QBO and report what would be synced without writing anything.
    """
    try:
        # Create start time variable
        start_time = datetime.now(timezone.utc)
        start_time_str = _normalize_last_sync(start_time.isoformat())
        logger.info(f"QBO Account sync triggered at: {start_time_str}")

        # Initialize services
        sync_service = SyncService()
        qbo_account_service = QboAccountService()
        auth_service = QboAuthService()

        # Get realm ID
        all_auths = auth_service.read_all()
        if not all_auths or len(all_auths) == 0:
            raise ValueError("No QBO authentication found. Please connect your QuickBooks account first.")
        realm_id = all_auths[0].realm_id
        logger.info(f"Using realm_id: {realm_id}")

        # Get or create Sync record
        provider = 'qbo'
        entity = 'account'
        env = 'prod'

        sync_record = _get_or_create_sync_record(sync_service, provider, env, entity)

        # Get last sync time for incremental sync
        last_sync_time = None
        if sync_record and sync_record.last_sync_datetime:
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
            qbo_account_service=qbo_account_service,
            reconcile_deletes=reconcile_deletes,
        )

        # Update Sync record
        end_time = datetime.now(timezone.utc)
        end_time_str = _normalize_last_sync(end_time.isoformat())

        if skip_sync_record_update:
            logger.info("Skipping sync record update (--skip-sync-update flag)")
            updated_sync = sync_record
        else:
            updated_sync = _update_sync_record(sync_service, sync_record, end_time_str)

        result = {
            "success": True,
            "realm_id": realm_id,
            "start_time": start_time_str,
            "end_time": end_time_str,
            "sync_record": updated_sync.to_dict(),
            "qbo_to_local": qbo_to_local_result,
        }

        logger.info(f"QBO Account sync completed. Accounts synced: {qbo_to_local_result['accounts_synced']}")

        return {
            "result": result,
            "status_code": 200,
        }

    except Exception as e:
        error_msg = f"Error syncing QBO Accounts: {str(e)}"
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
        description='Sync QBO Accounts to BuildOne (one-way: QBO -> local)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full incremental sync (uses last sync timestamp)
  python scripts/sync_qbo_account.py

  # Dry run: see what would be synced without writing anything
  python scripts/sync_qbo_account.py --dry-run

  # Full sync without updating sync record
  python scripts/sync_qbo_account.py --skip-sync-update

  # Full sync with delete reconciliation (deactivates local records not in QBO)
  python scripts/sync_qbo_account.py --reconcile-deletes

Note: --reconcile-deletes only works on full syncs (no last_sync_datetime).
It compares ALL local records against the full QBO account list and deactivates
any local records whose QboId is no longer present in QBO.
        """
    )

    parser.add_argument(
        '--skip-sync-update',
        action='store_true',
        help='Skip updating the sync record timestamp.'
    )

    parser.add_argument(
        '--reconcile-deletes',
        action='store_true',
        help='Deactivate local accounts not found in QBO. Only runs on full sync (no prior sync timestamp).'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Fetch from QBO and report what would be synced without writing to the database.'
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    result = sync_qbo_account(
        skip_sync_record_update=args.skip_sync_update,
        reconcile_deletes=args.reconcile_deletes,
        dry_run=args.dry_run,
    )

    import json
    print(json.dumps(result, indent=2, default=str))
