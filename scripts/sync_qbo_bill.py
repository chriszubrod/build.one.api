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
from integrations.intuit.qbo.bill.business.service import QboBillService
from integrations.intuit.qbo.bill.business.model import QboBill
from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector
from integrations.intuit.qbo.bill.connector.bill.persistence.repo import BillBillRepository
from integrations.intuit.qbo.bill.persistence.repo import QboBillRepository, QboBillLineRepository
from integrations.intuit.qbo.auth.business.service import QboAuthService
from integrations.intuit.qbo.attachable.business.service import QboAttachableService
from integrations.intuit.qbo.attachable.connector.attachment.business.service import AttachableAttachmentConnector
from integrations.intuit.qbo.attachable.connector.attachment.persistence.repo import AttachableAttachmentRepository
from integrations.intuit.qbo.bill.connector.bill_line_item.persistence.repo import BillLineItemBillLineRepository
from modules.bill.business.service import BillService
from modules.bill_line_item.business.service import BillLineItemService
from modules.bill_line_item_attachment.business.service import BillLineItemAttachmentService
from modules.attachment.business.service import AttachmentService

logger = logging.getLogger(__name__)

# Configure logging for script execution
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Sync configuration
BATCH_SIZE = 10  # Process bills in batches
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


def _link_attachments_to_bill_line_items(
    bill_id: int,
    qbo_attachables: list,
) -> int:
    """
    Link synced attachments to all BillLineItems for a Bill.
    
    If there are multiple line items and one attachment, the same attachment
    is linked to each line item via the BillLineItemAttachment mapping table.
    
    Args:
        bill_id: Database ID of the Bill in our system
        qbo_attachables: List of QboAttachable records that were synced
    
    Returns:
        int: Number of BillLineItemAttachment links created
    """
    if not qbo_attachables:
        return 0
    
    # Initialize services
    bill_line_item_service = BillLineItemService()
    attachment_service = AttachmentService()
    bill_line_item_attachment_service = BillLineItemAttachmentService()
    attachable_attachment_repo = AttachableAttachmentRepository()
    
    # Get all BillLineItems for this Bill
    bill_line_items = bill_line_item_service.read_by_bill_id(bill_id=bill_id)
    if not bill_line_items:
        logger.debug(f"No BillLineItems found for Bill {bill_id}")
        return 0
    
    links_created = 0
    
    # For each attachment, link to each line item
    for qbo_attachable in qbo_attachables:
        # Get the Attachment record via the AttachableAttachment mapping
        mapping = attachable_attachment_repo.read_by_qbo_attachable_id(qbo_attachable.id)
        if not mapping:
            logger.debug(f"No Attachment mapping found for QboAttachable {qbo_attachable.id}")
            continue
        
        attachment = attachment_service.read_by_id(mapping.attachment_id)
        if not attachment:
            logger.debug(f"Attachment {mapping.attachment_id} not found")
            continue
        
        # Link this attachment to each BillLineItem
        for line_item in bill_line_items:
            try:
                bill_line_item_attachment_service.create(
                    bill_line_item_public_id=line_item.public_id,
                    attachment_public_id=attachment.public_id,
                )
                links_created += 1
                logger.debug(f"Linked Attachment {attachment.id} to BillLineItem {line_item.id}")
            except Exception as e:
                # May fail if already linked (1-1 constraint) - that's OK
                logger.debug(f"Could not link Attachment {attachment.id} to BillLineItem {line_item.id}: {e}")
    
    if links_created > 0:
        logger.info(f"Created {links_created} BillLineItemAttachment links for Bill {bill_id}")
    
    return links_created


def sync_qbo_to_local(
    realm_id: str,
    last_sync_time: Optional[str],
    qbo_bill_service: QboBillService,
    bill_connector: BillBillConnector,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    sync_attachments: bool = False,
    attachable_service: Optional[QboAttachableService] = None,
) -> dict:
    """
    Sync Bills from QBO API to local database and modules.
    
    Args:
        realm_id: QBO realm ID
        last_sync_time: Last sync timestamp for incremental sync
        qbo_bill_service: QboBillService instance
        bill_connector: BillBillConnector instance
        start_date: Optional start date (YYYY-MM-DD) for filtering by TxnDate
        end_date: Optional end date (YYYY-MM-DD) for filtering by TxnDate
        sync_attachments: If True, also sync attachments for each bill
        attachable_service: QboAttachableService instance (required if sync_attachments is True)
    
    Returns:
        dict: Sync results including bills synced
    """
    if start_date or end_date:
        logger.info(f"Syncing Bills from QBO API for realm_id: {realm_id} (TxnDate: {start_date or 'beginning'} to {end_date or 'now'})")
    else:
        logger.info(f"Syncing Bills from QBO API for realm_id: {realm_id}")
    
    # Fetch bills from QBO and store locally (without auto-syncing to modules)
    bills = qbo_bill_service.sync_from_qbo(
        realm_id=realm_id,
        last_updated_time=last_sync_time,
        start_date=start_date,
        end_date=end_date,
        sync_to_modules=False  # We'll handle module sync separately for better control
    )
    
    if not bills:
        logger.info(f"No Bill updates found since {last_sync_time or 'beginning'}")
        return {
            "bills_synced": 0,
            "bills_module_synced": 0,
            "bills": [],
        }
    
    logger.info(f"Retrieved {len(bills)} bills from QBO")
    
    # Sync bills to Bill module
    bills_module_synced = 0
    attachments_synced = 0
    failed_bills = []
    
    for i, bill in enumerate(bills):
        try:
            # Get bill lines for this bill
            bill_lines = qbo_bill_service.read_lines_by_qbo_bill_id(bill.id)
            
            # Use retry logic for transient errors
            bill_module = with_retry(
                bill_connector.sync_from_qbo_bill,
                bill,
                bill_lines,
                max_retries=MAX_RETRIES,
                initial_delay=INITIAL_RETRY_DELAY,
            )
            bills_module_synced += 1
            logger.info(f"Synced QboBill {bill.id} to Bill {bill_module.id}")
            
            # Sync attachments for this bill if requested
            if sync_attachments and attachable_service and bill.qbo_id:
                try:
                    bill_attachables = attachable_service.sync_attachables_for_bill(
                        realm_id=realm_id,
                        bill_qbo_id=bill.qbo_id,
                        sync_to_modules=True,
                    )
                    attachments_synced += len(bill_attachables)
                    if bill_attachables:
                        logger.info(f"Synced {len(bill_attachables)} attachments for Bill {bill.qbo_id}")
                        
                        # Link attachments to each BillLineItem for this bill
                        _link_attachments_to_bill_line_items(
                            bill_id=bill_module.id,
                            qbo_attachables=bill_attachables,
                        )
                except Exception as att_e:
                    logger.error(f"Failed to sync attachments for Bill {bill.qbo_id}: {att_e}")
        except Exception as e:
            logger.error(f"Failed to sync QboBill {bill.id} to Bill: {e}")
            failed_bills.append(bill.id)
        
        # Add delay between batches to keep connection alive
        if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(bills):
            logger.debug(f"Processed {i + 1}/{len(bills)} bills, pausing...")
            time.sleep(BATCH_DELAY)
    
    if failed_bills:
        logger.warning(f"Failed to sync {len(failed_bills)} bills: {failed_bills}")
    
    return {
        "bills_synced": len(bills),
        "bills_module_synced": bills_module_synced,
        "attachments_synced": attachments_synced,
        "bills": [bill.to_dict() for bill in bills],
    }


def sync_local_to_qbo(
    realm_id: str,
    last_sync_time: Optional[str],
    qbo_bill_service: QboBillService,
    bill_mapping_repo: BillBillRepository,
    qbo_bill_repo: QboBillRepository,
    sync_attachments: bool = True,
) -> dict:
    """
    Sync finalized local Bills to QBO.
    
    This is the reverse sync: local Bills -> QBO Bills.
    
    This method:
    1. Reads finalized Bills (is_draft = False) modified since last_sync_time
    2. Filters to bills without existing QBO mapping
    3. Creates Bill in QBO via API
    4. Optionally syncs attachments to QBO
    
    Args:
        realm_id: QBO realm ID
        last_sync_time: Last sync timestamp - only bills modified after this time will be considered
        qbo_bill_service: QboBillService instance
        bill_mapping_repo: BillBillRepository instance
        qbo_bill_repo: QboBillRepository instance
        sync_attachments: If True, also sync attachments for each bill
    
    Returns:
        dict: Sync results including bills_pushed, attachments_pushed, errors
    """
    logger.info("Checking for local Bills to push to QBO")
    
    bills_pushed = 0
    attachments_pushed = 0
    errors = []
    
    # Initialize services
    bill_service = BillService()
    bill_connector = BillBillConnector()
    bill_line_item_service = BillLineItemService()
    bill_line_item_attachment_service = BillLineItemAttachmentService()
    attachment_service = AttachmentService()
    attachment_connector = AttachableAttachmentConnector()
    
    # Get all finalized bills (is_draft = False)
    logger.info("Loading all bills from database...")
    all_bills = bill_service.read_all()
    finalized_bills = [b for b in all_bills if b.is_draft is False]
    logger.info(f"Found {len(finalized_bills)} finalized bills")
    
    def parse_modified_datetime(dt_str):
        """Parse modified_datetime string to datetime for comparison."""
        if not dt_str:
            return None
        try:
            # Handle format "YYYY-MM-DD HH:MM:SS" from database
            return datetime.strptime(dt_str[:19], "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return None
    
    # Load ALL existing mappings in ONE query (optimization - avoid N+1 queries)
    logger.info("Loading existing QBO Bill mappings...")
    mapped_bill_ids = bill_mapping_repo.read_all_bill_ids()
    logger.info(f"Found {len(mapped_bill_ids)} existing mappings")
    
    # Find finalized bills without QBO mapping - these are candidates for push
    # This includes bills that failed on previous runs (retry mechanism)
    unmapped_bills = []
    for bill in finalized_bills:
        bill_id = int(bill.id) if isinstance(bill.id, str) else bill.id
        if bill_id not in mapped_bill_ids:
            unmapped_bills.append(bill)
    
    logger.info(f"Found {len(unmapped_bills)} finalized bills without QBO mapping")
    
    # Safety check for first run: if too many unmapped bills and no sync time, limit scope
    if not last_sync_time:
        if len(unmapped_bills) > 100:
            logger.warning(f"No last_sync_time and {len(unmapped_bills)} unmapped bills - skipping to avoid processing all historical bills")
            return {
                "bills_pushed": 0,
                "attachments_pushed": 0,
                "errors": [],
            }
        else:
            # Small number of unmapped bills is OK to process
            logger.info(f"No last_sync_time but only {len(unmapped_bills)} unmapped bills - processing all")
            bills_to_push = unmapped_bills
    else:
        # With a sync time, we process:
        # 1. All unmapped bills modified after last_sync_time (new bills)
        # 2. Plus any unmapped bills that were modified before (retry failed bills)
        # To prevent infinite retries of truly broken bills, limit retry batch size
        
        new_bills = []
        retry_bills = []
        
        for bill in unmapped_bills:
            mod_dt = parse_modified_datetime(bill.modified_datetime) if bill.modified_datetime else None
            if mod_dt and mod_dt > last_sync_time:
                new_bills.append(bill)
            else:
                retry_bills.append(bill)
        
        logger.info(f"New bills (modified after last sync): {len(new_bills)}")
        logger.info(f"Retry candidates (unmapped, modified before last sync): {len(retry_bills)}")
        
        # Process all new bills, plus up to 20 retry bills per run
        MAX_RETRIES_PER_RUN = 20
        if len(retry_bills) > MAX_RETRIES_PER_RUN:
            logger.info(f"Limiting retry bills to {MAX_RETRIES_PER_RUN} per run")
            retry_bills = retry_bills[:MAX_RETRIES_PER_RUN]
        
        bills_to_push = new_bills + retry_bills
    
    logger.info(f"Total bills to push: {len(bills_to_push)}")
    
    if not bills_to_push:
        return {
            "bills_pushed": 0,
            "attachments_pushed": 0,
            "errors": [],
        }
    
    # Process each bill
    for i, bill in enumerate(bills_to_push):
        try:
            logger.info(f"Pushing Bill {bill.id} ({bill.bill_number}) to QBO ({i+1}/{len(bills_to_push)})")
            
            # Create Bill in QBO
            qbo_bill = with_retry(
                bill_connector.sync_to_qbo_bill,
                bill,
                realm_id,
                max_retries=MAX_RETRIES,
                initial_delay=INITIAL_RETRY_DELAY,
            )
            
            bills_pushed += 1
            logger.info(f"Created QBO Bill {qbo_bill.qbo_id} for local Bill {bill.id}")
            
            # Sync attachments if requested
            if sync_attachments and qbo_bill.qbo_id:
                try:
                    att_count = _sync_bill_attachments_to_qbo(
                        bill=bill,
                        qbo_bill_id=qbo_bill.qbo_id,
                        realm_id=realm_id,
                        bill_line_item_service=bill_line_item_service,
                        bill_line_item_attachment_service=bill_line_item_attachment_service,
                        attachment_service=attachment_service,
                        attachment_connector=attachment_connector,
                    )
                    attachments_pushed += att_count
                except Exception as att_e:
                    logger.error(f"Failed to sync attachments for Bill {bill.id}: {att_e}")
                    errors.append({
                        "bill_id": bill.id,
                        "bill_number": bill.bill_number,
                        "error": f"Attachment sync failed: {str(att_e)}",
                    })
            
        except Exception as e:
            logger.error(f"Failed to push Bill {bill.id} to QBO: {e}")
            errors.append({
                "bill_id": bill.id,
                "bill_number": bill.bill_number,
                "error": str(e),
            })
        
        # Add delay between batches
        if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(bills_to_push):
            logger.debug(f"Processed {i + 1}/{len(bills_to_push)} bills, pausing...")
            time.sleep(BATCH_DELAY)
    
    logger.info(f"Pushed {bills_pushed} bills and {attachments_pushed} attachments to QBO")
    if errors:
        logger.warning(f"Encountered {len(errors)} errors during push")
    
    return {
        "bills_pushed": bills_pushed,
        "attachments_pushed": attachments_pushed,
        "errors": errors,
    }


def _sync_bill_attachments_to_qbo(
    bill,
    qbo_bill_id: str,
    realm_id: str,
    bill_line_item_service: BillLineItemService,
    bill_line_item_attachment_service: BillLineItemAttachmentService,
    attachment_service: AttachmentService,
    attachment_connector: AttachableAttachmentConnector,
) -> int:
    """
    Sync all attachments for a Bill's line items to QBO.
    
    Args:
        bill: Local Bill record
        qbo_bill_id: QBO Bill ID (string)
        realm_id: QBO realm ID
        Various service instances
    
    Returns:
        int: Number of attachments successfully synced
    """
    bill_id = int(bill.id) if isinstance(bill.id, str) else bill.id
    
    # Get all line items for this bill
    line_items = bill_line_item_service.read_by_bill_id(bill_id=bill_id)
    if not line_items:
        return 0
    
    attachments_synced = 0
    synced_attachment_ids = set()  # Track to avoid duplicates
    
    for line_item in line_items:
        if not line_item.public_id:
            continue
        
        # Get attachment for this line item
        attachment_link = bill_line_item_attachment_service.read_by_bill_line_item_id(
            bill_line_item_public_id=line_item.public_id
        )
        
        if not attachment_link or not attachment_link.attachment_id:
            continue
        
        # Skip if already synced (same attachment on multiple line items)
        if attachment_link.attachment_id in synced_attachment_ids:
            continue
        
        # Get attachment record
        attachment = attachment_service.read_by_id(id=attachment_link.attachment_id)
        if not attachment or not attachment.blob_url:
            logger.warning(f"Attachment {attachment_link.attachment_id} not found or missing blob_url")
            continue
        
        try:
            # Sync attachment to QBO
            attachment_connector.sync_attachment_to_qbo(
                attachment=attachment,
                realm_id=realm_id,
                entity_type="Bill",
                entity_id=qbo_bill_id,
            )
            
            synced_attachment_ids.add(attachment_link.attachment_id)
            attachments_synced += 1
            logger.debug(f"Synced attachment {attachment.id} to QBO Bill {qbo_bill_id}")
            
        except Exception as e:
            logger.error(f"Failed to sync attachment {attachment.id} to QBO: {e}")
    
    return attachments_synced


def sync_qbo_bill(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    skip_sync_record_update: bool = False,
    sync_attachments: bool = True,
) -> dict:
    """
    Two-way sync for QBO Bills <-> Bill module.
    
    1. QBO -> Local: Fetch bills modified since last sync, store locally, sync to Bill
    2. Local -> QBO: Check for locally modified Bills, sync back to QboBill
    
    Args:
        start_date: Optional start date (YYYY-MM-DD) for filtering bills by TxnDate.
                   Use this for historical batch syncing.
        end_date: Optional end date (YYYY-MM-DD) for filtering bills by TxnDate.
                 Use this for historical batch syncing.
        skip_sync_record_update: If True, don't update the sync record timestamp.
                                Use this when doing historical batch syncs to preserve
                                the incremental sync tracking.
        sync_attachments: If True, also sync attachments for each bill.
    """
    try:
        # Create start time variable
        start_time = datetime.now(timezone.utc)
        start_time_str = _normalize_last_sync(start_time.isoformat())
        logger.info(f"QBO Bill sync triggered at: {start_time_str}")
        
        if start_date or end_date:
            logger.info(f"Date range filter: {start_date or 'beginning'} to {end_date or 'now'}")
        
        # Initialize services
        sync_service = SyncService()
        qbo_bill_service = QboBillService()
        qbo_bill_repo = QboBillRepository()
        bill_connector = BillBillConnector()
        bill_mapping_repo = BillBillRepository()
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
        entity = 'bill'
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
        
        # Step 1: Sync from QBO to local
        qbo_to_local_result = sync_qbo_to_local(
            realm_id=realm_id,
            last_sync_time=last_sync_time,
            qbo_bill_service=qbo_bill_service,
            bill_connector=bill_connector,
            start_date=start_date,
            end_date=end_date,
            sync_attachments=sync_attachments,
            attachable_service=attachable_service,
        )
        
        # Step 2: Sync from local to QBO (reverse sync)
        # Use a SEPARATE sync record for push operations to avoid conflicts with pull
        push_entity = 'bill_push'
        push_sync_record = _get_or_create_sync_record(sync_service, provider, env, push_entity)
        
        push_last_sync_time = None
        if push_sync_record and push_sync_record.last_sync_datetime:
            push_last_sync_time = push_sync_record.last_sync_datetime
            logger.info(f"Push sync - last sync time: {push_last_sync_time}")
        else:
            logger.info("No previous push sync found. Will only sync bills from now onward.")
            # For first run, set the cutoff to NOW to avoid syncing historical bills
            # Only bills finalized AFTER this point will be pushed to QBO
            push_last_sync_time = start_time_str
        
        local_to_qbo_result = sync_local_to_qbo(
            realm_id=realm_id,
            last_sync_time=push_last_sync_time,
            qbo_bill_service=qbo_bill_service,
            bill_mapping_repo=bill_mapping_repo,
            qbo_bill_repo=qbo_bill_repo,
            sync_attachments=sync_attachments,
        )
        
        # Update Sync records
        end_time = datetime.now(timezone.utc)
        end_time_str = _normalize_last_sync(end_time.isoformat())
        
        # Update Push Sync record (separate from pull sync record)
        if not skip_sync_record_update:
            _update_sync_record(sync_service, push_sync_record, end_time_str)
            logger.info(f"Updated push sync record to: {end_time_str}")
        
        # Update Pull Sync record
        if skip_sync_record_update:
            logger.info("Skipping sync record update (--skip-sync-update flag)")
            updated_sync = sync_record
        elif end_date:
            # When end_date is provided, use it as the sync record timestamp
            # This allows tracking progress through historical batch imports
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
            "local_to_qbo": local_to_qbo_result,
        }
        
        logger.info(f"QBO Bill sync completed. Bills from QBO: {qbo_to_local_result['bills_synced']}, "
                    f"Bills module synced: {qbo_to_local_result['bills_module_synced']}, "
                    f"Bills pushed: {local_to_qbo_result['bills_pushed']}")
        
        return {
            "result": result,
            "status_code": 200,
        }

    except Exception as e:
        error_msg = f"Error syncing QBO Bills: {str(e)}"
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
        description='Sync QBO Bills to BuildOne',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full incremental sync (uses last sync timestamp)
  python scripts/sync_qbo_bill.py

  # Sync bills for a specific year - sync record set to end_date for tracking
  python scripts/sync_qbo_bill.py --start-date 2022-01-01 --end-date 2022-12-31
  python scripts/sync_qbo_bill.py --start-date 2023-01-01 --end-date 2023-12-31
  python scripts/sync_qbo_bill.py --start-date 2024-01-01 --end-date 2024-12-31

  # Sync all bills from a start date to now (sync record set to current time)
  python scripts/sync_qbo_bill.py --start-date 2024-01-01

  # Historical batch without updating sync record
  python scripts/sync_qbo_bill.py --start-date 2022-01-01 --end-date 2022-12-31 --skip-sync-update

Note: When --end-date is provided, the sync record timestamp is set to the end_date,
allowing you to track progress through historical batch imports.
        """
    )
    
    parser.add_argument(
        '--start-date',
        type=str,
        help='Start date for filtering bills by TxnDate (YYYY-MM-DD). Inclusive.',
        default=None
    )
    
    parser.add_argument(
        '--end-date',
        type=str,
        help='End date for filtering bills by TxnDate (YYYY-MM-DD). Inclusive.',
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
        help='Skip syncing file attachments for each bill from QBO. By default, attachments are synced.'
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
    
    result = sync_qbo_bill(
        start_date=args.start_date,
        end_date=args.end_date,
        skip_sync_record_update=args.skip_sync_update,
        sync_attachments=not args.skip_attachments,
    )
    
    import json
    print(json.dumps(result, indent=2, default=str))
