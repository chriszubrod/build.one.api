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
from integrations.intuit.qbo.vendorcredit.business.service import QboVendorCreditService
from integrations.intuit.qbo.vendorcredit.business.model import QboVendorCredit
from integrations.intuit.qbo.vendorcredit.external.client import QboVendorCreditClient
from integrations.intuit.qbo.vendorcredit.persistence.repo import QboVendorCreditRepository
from integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service import VendorCreditBillCreditConnector
from integrations.intuit.qbo.auth.business.service import QboAuthService
from integrations.intuit.qbo.attachable.business.service import QboAttachableService
from integrations.intuit.qbo.attachable.connector.attachment.persistence.repo import AttachableAttachmentRepository
from entities.bill_credit_line_item.business.service import BillCreditLineItemService
from entities.attachment.business.service import AttachmentService
from entities.bill_credit_line_item_attachment.business.service import BillCreditLineItemAttachmentService

logger = logging.getLogger(__name__)

# Configure logging for script execution
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Sync configuration
BATCH_SIZE = 10  # Process vendor credits in batches
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


def _link_attachments_to_bill_credit_line_items(
    bill_credit_id: int,
    qbo_attachables: list,
) -> int:
    """
    Link synced attachments to all BillCreditLineItems for a BillCredit.

    If there are multiple line items and one attachment, the same attachment
    is linked to each line item via BillCreditLineItemAttachment.

    Args:
        bill_credit_id: Database ID of the BillCredit in our system
        qbo_attachables: List of QboAttachable records that were synced

    Returns:
        int: Number of BillCreditLineItemAttachment links created
    """
    if not qbo_attachables:
        return 0

    bill_credit_line_item_service = BillCreditLineItemService()
    attachment_service = AttachmentService()
    bill_credit_line_item_attachment_service = BillCreditLineItemAttachmentService()
    attachable_attachment_repo = AttachableAttachmentRepository()

    line_items = bill_credit_line_item_service.read_by_bill_credit_id(bill_credit_id=bill_credit_id)
    if not line_items:
        logger.debug(f"No BillCreditLineItems found for BillCredit {bill_credit_id}")
        return 0

    links_created = 0
    for qbo_attachable in qbo_attachables:
        mapping = attachable_attachment_repo.read_by_qbo_attachable_id(qbo_attachable.id)
        if not mapping:
            logger.debug(f"No Attachment mapping found for QboAttachable {qbo_attachable.id}")
            continue

        attachment = attachment_service.read_by_id(mapping.attachment_id)
        if not attachment:
            logger.debug(f"Attachment {mapping.attachment_id} not found")
            continue

        for line_item in line_items:
            try:
                bill_credit_line_item_attachment_service.create(
                    bill_credit_line_item_public_id=line_item.public_id,
                    attachment_public_id=attachment.public_id,
                )
                links_created += 1
                logger.debug(f"Linked Attachment {attachment.id} to BillCreditLineItem {line_item.id}")
            except Exception as e:
                logger.debug(f"Could not link Attachment {attachment.id} to BillCreditLineItem {line_item.id}: {e}")

    if links_created > 0:
        logger.info(f"Created {links_created} BillCreditLineItemAttachment links for BillCredit {bill_credit_id}")

    return links_created


def _dry_run_preview(
    realm_id: str,
    qbo_auth,
    last_sync_time: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """
    Dry-run preview: fetch vendor credits from QBO and report what would be synced
    without writing anything to the local database or pushing to QBO.
    """
    logger.info("[DRY RUN] Fetching vendor credits from QBO to preview sync (no writes will occur)...")

    with QboVendorCreditClient(access_token=qbo_auth.access_token, realm_id=realm_id) as client:
        qbo_vcs = client.query_all_vendor_credits(
            last_updated_time=last_sync_time,
            start_date=start_date,
            end_date=end_date,
        )

    logger.info(f"[DRY RUN] QBO returned {len(qbo_vcs)} vendor credits")

    # Check existing local QBO vendor credit records (read-only)
    vc_repo = QboVendorCreditRepository()
    existing = vc_repo.read_by_realm_id(realm_id)
    existing_qbo_ids = {vc.qbo_id for vc in existing}

    would_create = [vc for vc in qbo_vcs if vc.id not in existing_qbo_ids]
    would_update = [vc for vc in qbo_vcs if vc.id in existing_qbo_ids]

    logger.info(f"[DRY RUN] QBO staging table (qbo.VendorCredit):")
    logger.info(f"[DRY RUN]   {len(would_create)} would be CREATED")
    logger.info(f"[DRY RUN]   {len(would_update)} would be UPDATED")
    logger.info("[DRY RUN] No changes were made to the local database.")
    logger.info("[DRY RUN] No data was pushed to QBO.")

    sample = [
        {"qbo_id": vc.id, "doc_number": vc.doc_number, "vendor": vc.vendor_ref.name if vc.vendor_ref else None, "txn_date": vc.txn_date, "total": float(vc.total_amt) if vc.total_amt else None}
        for vc in would_create[:5]
    ]

    return {
        "dry_run": True,
        "direction": "QBO → BuildOne only (read-only from QBO)",
        "qbo_records_found": len(qbo_vcs),
        "qbo_staging": {
            "would_create": len(would_create),
            "would_update": len(would_update),
        },
        "sample_new_records": sample,
    }


def sync_qbo_to_local(
    realm_id: str,
    last_sync_time: Optional[str],
    qbo_vendor_credit_service: QboVendorCreditService,
    vendor_credit_connector: VendorCreditBillCreditConnector,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    sync_attachments: bool = True,
    attachable_service: Optional[QboAttachableService] = None,
) -> dict:
    """
    Sync VendorCredits from QBO API to local database and modules.

    Args:
        realm_id: QBO realm ID
        last_sync_time: Last sync timestamp for incremental sync
        qbo_vendor_credit_service: QboVendorCreditService instance
        vendor_credit_connector: VendorCreditBillCreditConnector instance
        start_date: Optional start date (YYYY-MM-DD) for filtering by TxnDate
        end_date: Optional end date (YYYY-MM-DD) for filtering by TxnDate
        sync_attachments: If True, also sync attachments for each vendor credit
        attachable_service: QboAttachableService instance (required if sync_attachments is True)

    Returns:
        dict: Sync results including vendor credits and attachments synced
    """
    if start_date or end_date:
        logger.info(f"Syncing VendorCredits from QBO API for realm_id: {realm_id} (TxnDate: {start_date or 'beginning'} to {end_date or 'now'})")
    else:
        logger.info(f"Syncing VendorCredits from QBO API for realm_id: {realm_id}")
    
    # Fetch vendor credits from QBO and store locally (without auto-syncing to modules)
    vendor_credits = qbo_vendor_credit_service.sync_from_qbo(
        realm_id=realm_id,
        last_updated_time=last_sync_time,
        start_date=start_date,
        end_date=end_date,
        sync_to_modules=False  # We'll handle module sync separately for better control
    )
    
    if not vendor_credits:
        logger.info(f"No VendorCredit updates found since {last_sync_time or 'beginning'}")
        return {
            "vendor_credits_synced": 0,
            "bill_credits_module_synced": 0,
            "vendor_credits": [],
        }
    
    logger.info(f"Retrieved {len(vendor_credits)} vendor credits from QBO")
    
    # Sync vendor credits to BillCredit module
    bill_credits_module_synced = 0
    attachments_synced = 0
    failed_vendor_credits = []

    for i, vendor_credit in enumerate(vendor_credits):
        try:
            # Get vendor credit lines for this vendor credit
            vendor_credit_lines = qbo_vendor_credit_service.read_lines_by_vendor_credit_id(vendor_credit.id)

            # Use retry logic for transient errors
            bill_credit = with_retry(
                vendor_credit_connector.sync_from_qbo_vendor_credit,
                vendor_credit,
                vendor_credit_lines,
                max_retries=MAX_RETRIES,
                initial_delay=INITIAL_RETRY_DELAY,
            )
            if bill_credit:
                bill_credits_module_synced += 1
                logger.info(f"Synced QboVendorCredit {vendor_credit.id} to BillCredit {bill_credit.id}")

                # Sync attachments for this vendor credit if requested
                if sync_attachments and attachable_service and vendor_credit.qbo_id:
                    try:
                        bill_attachables = attachable_service.sync_attachables_for_vendor_credit(
                            realm_id=realm_id,
                            vendor_credit_qbo_id=vendor_credit.qbo_id,
                            sync_to_modules=True,
                        )
                        attachments_synced += len(bill_attachables)
                        if bill_attachables:
                            logger.info(f"Synced {len(bill_attachables)} attachments for VendorCredit {vendor_credit.qbo_id}")
                            _link_attachments_to_bill_credit_line_items(
                                bill_credit_id=bill_credit.id,
                                qbo_attachables=bill_attachables,
                            )
                    except Exception as att_e:
                        logger.error(f"Failed to sync attachments for VendorCredit {vendor_credit.qbo_id}: {att_e}")

        except Exception as e:
            logger.error(f"Failed to sync QboVendorCredit {vendor_credit.id} to BillCredit: {e}")
            failed_vendor_credits.append(vendor_credit.id)

        # Add delay between batches to keep connection alive
        if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(vendor_credits):
            logger.debug(f"Processed {i + 1}/{len(vendor_credits)} vendor credits, pausing...")
            time.sleep(BATCH_DELAY)

    if failed_vendor_credits:
        logger.warning(f"Failed to sync {len(failed_vendor_credits)} vendor credits: {failed_vendor_credits}")

    return {
        "vendor_credits_synced": len(vendor_credits),
        "bill_credits_module_synced": bill_credits_module_synced,
        "attachments_synced": attachments_synced,
        "vendor_credits": [vc.to_dict() for vc in vendor_credits],
    }


def sync_qbo_vendorcredit(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    skip_sync_record_update: bool = False,
    sync_attachments: bool = True,
    dry_run: bool = False,
) -> dict:
    """
    Sync QBO VendorCredits to BillCredit module.

    1. QBO -> Local: Fetch vendor credits modified since last sync, store locally, sync to BillCredit
    2. Optionally sync attachments for each VendorCredit and link to BillCreditLineItems.

    Args:
        start_date: Optional start date (YYYY-MM-DD) for filtering vendor credits by TxnDate.
        end_date: Optional end date (YYYY-MM-DD) for filtering vendor credits by TxnDate.
        skip_sync_record_update: If True, don't update the sync record timestamp.
        sync_attachments: If True, sync file attachments for each vendor credit from QBO.
        dry_run: If True, fetch from QBO and report what would be synced without writing anything.
    """
    try:
        # Create start time variable
        start_time = datetime.now(timezone.utc)
        start_time_str = _normalize_last_sync(start_time.isoformat())
        logger.info(f"QBO VendorCredit sync triggered at: {start_time_str}")
        
        if start_date or end_date:
            logger.info(f"Date range filter: {start_date or 'beginning'} to {end_date or 'now'}")
        
        # Initialize services
        sync_service = SyncService()
        qbo_vendor_credit_service = QboVendorCreditService()
        vendor_credit_connector = VendorCreditBillCreditConnector()
        auth_service = QboAuthService()
        attachable_service = QboAttachableService() if sync_attachments else None
        
        # Get realm ID
        all_auths = auth_service.read_all()
        if not all_auths or len(all_auths) == 0:
            raise ValueError("No QBO authentication found. Please connect your QuickBooks account first.")
        realm_id = all_auths[0].realm_id
        logger.info(f"Using realm_id: {realm_id}")
        
        # Get or create Sync record
        provider = 'qbo'
        entity = 'vendorcredit'
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
            qbo_auth = auth_service.ensure_valid_token()
            if not qbo_auth or not qbo_auth.access_token:
                raise ValueError("No valid QBO access token found")
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
            qbo_vendor_credit_service=qbo_vendor_credit_service,
            vendor_credit_connector=vendor_credit_connector,
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
        
        logger.info(
            f"QBO VendorCredit sync completed. VendorCredits from QBO: {qbo_to_local_result['vendor_credits_synced']}, "
            f"BillCredits module synced: {qbo_to_local_result['bill_credits_module_synced']}, "
            f"attachments synced: {qbo_to_local_result.get('attachments_synced', 0)}"
        )
        
        return {
            "result": result,
            "status_code": 200,
        }

    except Exception as e:
        error_msg = f"Error syncing QBO VendorCredits: {str(e)}"
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
        description='Sync QBO VendorCredits to BuildOne BillCredit module',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full incremental sync (uses last sync timestamp)
  python scripts/sync_qbo_vendorcredit.py

  # Sync vendor credits for a specific year - sync record set to end_date for tracking
  python scripts/sync_qbo_vendorcredit.py --start-date 2022-01-01 --end-date 2022-12-31
  python scripts/sync_qbo_vendorcredit.py --start-date 2023-01-01 --end-date 2023-12-31
  python scripts/sync_qbo_vendorcredit.py --start-date 2024-01-01 --end-date 2024-12-31

  # Sync all vendor credits from a start date to now (sync record set to current time)
  python scripts/sync_qbo_vendorcredit.py --start-date 2024-01-01

  # Historical batch without updating sync record
  python scripts/sync_qbo_vendorcredit.py --start-date 2022-01-01 --end-date 2022-12-31 --skip-sync-update

Note: When --end-date is provided, the sync record timestamp is set to the end_date,
allowing you to track progress through historical batch imports.
        """
    )
    
    parser.add_argument(
        '--start-date',
        type=str,
        help='Start date for filtering vendor credits by TxnDate (YYYY-MM-DD). Inclusive.',
        default=None
    )
    
    parser.add_argument(
        '--end-date',
        type=str,
        help='End date for filtering vendor credits by TxnDate (YYYY-MM-DD). Inclusive.',
        default=None
    )
    
    parser.add_argument(
        '--skip-sync-update',
        action='store_true',
        help='Skip updating the sync record timestamp. Use for historical batch imports.'
    )

    parser.add_argument(
        '--skip-attachments',
        action='store_true',
        help='Skip syncing file attachments for each vendor credit from QBO. By default, attachments are synced.'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Fetch from QBO and report what would be synced without writing to the database or pushing to QBO.'
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
    
    result = sync_qbo_vendorcredit(
        start_date=args.start_date,
        end_date=args.end_date,
        skip_sync_record_update=args.skip_sync_update,
        sync_attachments=not args.skip_attachments,
        dry_run=args.dry_run,
    )
    
    import json
    print(json.dumps(result, indent=2, default=str))
