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
from scripts.sync_helper import (
    WatermarkRun,
    _normalize_last_sync,
    _normalize_watermark_value,
    assert_cli_system_admin,
)
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from shared.database import with_retry
from integrations.intuit.qbo.base.pull_race import read_lines_riding_out_race, header_has_amount
from integrations.sync.business.service import SyncService
from integrations.intuit.qbo.bill.business.service import QboBillService
from integrations.intuit.qbo.bill.business.model import QboBill
from integrations.intuit.qbo.bill.external.client import QboBillClient
from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector
from integrations.intuit.qbo.bill.connector.bill.persistence.repo import BillBillRepository
from integrations.intuit.qbo.bill.persistence.repo import QboBillRepository, QboBillLineRepository
from integrations.intuit.qbo.auth.business.service import QboAuthService
from integrations.intuit.qbo.attachable.business.service import QboAttachableService
from integrations.intuit.qbo.attachable.connector.attachment.business.service import AttachableAttachmentConnector
from integrations.intuit.qbo.attachable.connector.attachment.persistence.repo import AttachableAttachmentRepository
from integrations.intuit.qbo.bill.connector.bill_line_item.persistence.repo import BillLineItemBillLineRepository
from entities.bill.business.service import BillService
from entities.bill_line_item.business.service import BillLineItemService
from entities.bill_line_item_attachment.business.service import BillLineItemAttachmentService
from entities.attachment.business.service import AttachmentService

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

    # Pre-load existing links once, then track within-run links in the same set —
    # avoids an N+1 re-query (each per-line read also re-resolved public_id->id) on
    # every (attachment x line item) iteration.
    linked_public_ids = {
        a.bill_line_item_public_id
        for a in bill_line_item_attachment_service.read_by_bill_line_item_ids(
            [li.public_id for li in bill_line_items if li.public_id]
        )
    }

    # For each attachment, link to each line item
    for qbo_attachable in qbo_attachables:
        # Get the Attachment record via the AttachableAttachment mapping
        mapping = attachable_attachment_repo.read_by_qbo_attachable_id(qbo_attachable.id)
        if not mapping:
            logger.debug(f"No Attachment mapping found for QboAttachable {qbo_attachable.id}")
            continue
        
        attachment = attachment_service.read_by_id(mapping.attachment_id)
        if not attachment or not attachment.public_id:
            logger.debug(f"Attachment {mapping.attachment_id} not found")
            continue

        # BillLineItemAttachment is 1:1 — each line item holds at most one attachment.
        # Link this attachment to any line items not yet linked; pre-check existing so a
        # real failure isn't silently swallowed as "already linked".
        attachment_linked_count = 0
        for line_item in bill_line_items:
            if not line_item.public_id or line_item.public_id in linked_public_ids:
                continue
            try:
                bill_line_item_attachment_service.create(
                    bill_line_item_public_id=line_item.public_id,
                    attachment_public_id=attachment.public_id,
                )
                links_created += 1
                attachment_linked_count += 1
                linked_public_ids.add(line_item.public_id)
                logger.debug(f"Linked Attachment {attachment.id} to BillLineItem {line_item.id}")
            except Exception as e:
                logger.debug(f"Could not link Attachment {attachment.id} to BillLineItem {line_item.id}: {e}")
        if attachment_linked_count == 0:
            logger.warning(
                f"Bill {bill_id}: Attachment {attachment.id} (QboAttachable {qbo_attachable.id}) "
                f"could not be linked — all {len(bill_line_items)} line item(s) already have an attachment. "
                f"BillLineItemAttachment is 1:1; this attachment is unlinked."
            )
    
    if links_created > 0:
        logger.info(f"Created {links_created} BillLineItemAttachment links for Bill {bill_id}")
    
    return links_created


def _dry_run_preview(
    realm_id: str,
    qbo_auth,
    last_sync_time: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """
    Dry-run preview: fetch bills from QBO and report what would be synced
    without writing anything to the local database or pushing to QBO.
    """
    logger.info("[DRY RUN] Fetching bills from QBO to preview sync (no writes will occur)...")

    with QboBillClient(realm_id=realm_id) as client:
        qbo_bills = client.query_all_bills(
            last_updated_time=last_sync_time,
            start_date=start_date,
            end_date=end_date,
        )

    logger.info(f"[DRY RUN] QBO returned {len(qbo_bills)} bills")

    # Check existing local QBO bill records (read-only)
    bill_repo = QboBillRepository()
    existing = bill_repo.read_by_realm_id(realm_id)
    existing_qbo_ids = {b.qbo_id for b in existing}

    # Check how many local Bills already have QBO mappings (read-only)
    mapping_repo = BillBillRepository()
    mapped_bill_ids = mapping_repo.read_all_bill_ids()
    mapped_count = len(mapped_bill_ids) if mapped_bill_ids else 0

    would_create = [b for b in qbo_bills if b.id not in existing_qbo_ids]
    would_update = [b for b in qbo_bills if b.id in existing_qbo_ids]

    logger.info(f"[DRY RUN] QBO staging table (qbo.Bill):")
    logger.info(f"[DRY RUN]   {len(would_create)} would be CREATED")
    logger.info(f"[DRY RUN]   {len(would_update)} would be UPDATED")
    logger.info(f"[DRY RUN] Bill module mappings already in place: {mapped_count}")
    logger.info("[DRY RUN] No changes were made to the local database.")
    logger.info("[DRY RUN] No data was pushed to QBO.")
    logger.info("[DRY RUN] IMPORTANT: sync_qbo_bill.py has bidirectional capability.")
    logger.info("[DRY RUN] Always use --pull-only for one-way QBO -> BuildOne syncs.")

    sample = [
        {"qbo_id": b.id, "doc_number": b.doc_number, "vendor": b.vendor_ref.name if b.vendor_ref else None, "txn_date": b.txn_date, "total": float(b.total_amt) if b.total_amt else None}
        for b in would_create[:5]
    ]

    return {
        "dry_run": True,
        "direction": "QBO → BuildOne only (read-only from QBO, --pull-only enforced for production)",
        "qbo_records_found": len(qbo_bills),
        "qbo_staging": {
            "would_create": len(would_create),
            "would_update": len(would_update),
        },
        "bill_module_mappings_existing": mapped_count,
        "sample_new_records": sample,
        "warning": "This script supports bidirectional sync. Always use --pull-only for QBO -> BuildOne only.",
    }


def sync_qbo_to_local(
    realm_id: str,
    last_sync_time: Optional[str],
    qbo_bill_service: QboBillService,
    bill_connector: BillBillConnector,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    sync_attachments: bool = False,
    attachable_service: Optional[QboAttachableService] = None,
) -> tuple[dict, SyncOutcome]:
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
        tuple[dict, SyncOutcome]: Sync results envelope and service pull outcome
    """
    if start_date or end_date:
        logger.info(f"Syncing Bills from QBO API for realm_id: {realm_id} (TxnDate: {start_date or 'beginning'} to {end_date or 'now'})")
    else:
        logger.info(f"Syncing Bills from QBO API for realm_id: {realm_id}")
    
    # Fetch and upsert QboBill/QboBillLine mirror records only.
    # Module sync (Bill/BillLineItem) is handled below per-bill with retry logic
    # and attachment sync — passing sync_to_modules=True here would double-sync.
    outcome = qbo_bill_service.sync_from_qbo(
        realm_id=realm_id,
        last_updated_time=last_sync_time,
        start_date=start_date,
        end_date=end_date,
        sync_to_modules=False,
        reconcile_deletes=True,  # full-sync-only guard inside: removes local records deleted in QBO
    )
    bills = outcome.synced
    
    if not bills:
        logger.info(f"No Bill updates found since {last_sync_time or 'beginning'}")
        return {
            "bills_synced": 0,
            "bills_module_synced": 0,
            "bills": [],
            "skipped_count": 0,
            "skipped_bill_ids": [],
            "failed_count": outcome.failed_count,
            # failed_bill_ids: qbo.Bill staging PKs; staging_failed_qbo_ids: QBO API Ids
            "failed_bill_ids": outcome.projection_failed_ids,
            "staging_failed_qbo_ids": outcome.staging_failed_ids,
        }, outcome
    
    logger.info(f"Retrieved {len(bills)} bills from QBO")
    
    # Sync bills to Bill module
    attachments_synced = 0
    excel_rows_synced = 0
    sharepoint_uploads_synced = 0
    box_excel_batches = 0
    synced_bills = []    # (bill, bill_id) — collected for batched budget-tracker Excel sync
    bill_service = BillService()
    bill_line_item_service = BillLineItemService()

    for i, bill in enumerate(bills):
        try:
            # Get bill lines, re-reading to ride out the cross-process pull-race (an empty read
            # colliding with a non-zero header). If the lines never arrive, DEFER the row: skip
            # it WITHOUT adding to failed_bills so the watermark advances — this avoids stalling
            # the whole sync on a genuinely line-less record. Recovery for the (rare) genuinely-
            # line-less case: the daily QBO reconcile COUNTS unprojected bills (auto-recreate is
            # gated on QBO_RECONCILE_BILL_AUTOFIX, default off → surfaced, not recreated); and any
            # OTHER real failure that holds the watermark re-pulls this row next tick (slow race).
            bill_lines = read_lines_riding_out_race(
                qbo_bill_service.read_lines_by_qbo_bill_id, bill.id, bill.total_amt
            )
            if not bill_lines and header_has_amount(bill.total_amt):
                logger.warning(
                    f"Deferring QboBill {bill.id} (qbo_id={bill.qbo_id}): no lines for non-zero "
                    f"header {bill.total_amt} after re-read (pull race or genuinely line-less); "
                    f"skipping so the watermark advances (the daily QBO reconcile counts unprojected bills)."
                )
                continue

            # Use retry logic for transient errors
            bill_module = with_retry(
                bill_connector.sync_from_qbo_bill,
                bill,
                bill_lines,
                max_retries=MAX_RETRIES,
                initial_delay=INITIAL_RETRY_DELAY,
            )
            outcome.record_projected()
            logger.info(f"Synced QboBill {bill.id} to Bill {bill_module.id}")
            # Collect for batched budget-tracker Excel sync after the loop
            synced_bills.append((bill_module, bill_module.id))

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
            outcome.record_projection_error(bill.id, e, label="QboBill->Bill", logger=logger)

        # Add delay between batches to keep connection alive
        if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(bills):
            logger.debug(f"Processed {i + 1}/{len(bills)} bills, pausing...")
            time.sleep(BATCH_DELAY)

    # --- Batch budget-tracker Excel sync: one worksheet read + batched insert per project ---
    # Mirrors the purchase pull (sync_expenses_batch_to_excel). Best-effort: an Excel
    # failure is logged and never blocks the bill watermark.
    if synced_bills:
        project_bill_map = {}  # project_id -> [(bill, [line_items_for_this_project])]
        bill_line_counts = {}  # bill.id -> total line count (SharePoint filename parity with completion)
        for bill, bill_id in synced_bills:
            try:
                blis = bill_line_item_service.read_by_bill_id(bill_id=bill_id)
                bill_line_counts[bill.id] = len(blis)
                by_project = {}
                for bli in blis:
                    if bli.project_id:
                        by_project.setdefault(bli.project_id, []).append(bli)
                for proj_id, proj_items in by_project.items():
                    project_bill_map.setdefault(proj_id, []).append((bill, proj_items))
            except Exception as e:
                logger.warning(f"Could not read line items for Bill {bill_id} for Excel sync: {e}")

        if project_bill_map:
            logger.info(f"Excel sync: {len(project_bill_map)} project(s) to sync across {len(synced_bills)} bill(s)")
        for proj_id, bill_line_pairs in project_bill_map.items():
            try:
                excel_result = bill_service.sync_bills_batch_to_excel(
                    bill_line_pairs=bill_line_pairs,
                    project_id=proj_id,
                )
                excel_rows_synced += excel_result.get("synced_count", 0)
                if excel_result.get("errors"):
                    for err in excel_result["errors"]:
                        logger.warning(f"Excel sync error for project {proj_id}: {err}")
            except Exception as excel_e:
                logger.warning(f"Could not sync bills to Excel for project {proj_id}: {excel_e}")

        # --- SharePoint document upload (best-effort) ---
        # Re-pull-safe without a synced-guard: the pull is incremental (watermark), so an
        # unchanged bill is never re-fetched; when a bill IS re-pulled (modified in QBO, or a
        # rare full sync) the upload uses conflictBehavior=replace, refreshing the same-named
        # file rather than creating a duplicate.
        for proj_id, bill_line_pairs in project_bill_map.items():
            for bill, proj_items in bill_line_pairs:
                try:
                    sp_result = bill_service._upload_attachments_to_module_folder(
                        bill=bill,
                        line_items=proj_items,
                        project_id=proj_id,
                        bill_line_items_count=bill_line_counts.get(bill.id, len(proj_items)),
                    )
                    sharepoint_uploads_synced += sp_result.get("synced_count", 0)
                    if sp_result.get("errors"):
                        for err in sp_result["errors"]:
                            logger.warning(f"SharePoint upload error for project {proj_id}: {err}")
                except Exception as sp_e:
                    logger.warning(f"Could not upload bill attachments to SharePoint for project {proj_id}: {sp_e}")

        # --- Box: doc-push (PDFs -> project's "14 - Invoices") + BATCHED Box Excel ---
        # Best-effort, ALLOW_BOX_WRITES-gated. Box Excel is batched per project (one
        # download/edit/upload per workbook for all the project's pulled bills) since the
        # drain rewrites the whole .xlsx; per-entity would churn one Box version per bill.
        import os as _os
        if _os.getenv("ALLOW_BOX_WRITES", "").strip().lower() == "true":
            from integrations.box.outbox.business.service import BoxOutboxService
            from integrations.box.excel.business.mapping_service import BoxProjectWorkbookService
            _box_outbox = BoxOutboxService()
            _box_workbook = BoxProjectWorkbookService()
            for proj_id, bill_line_pairs in project_bill_map.items():
                for bill, proj_items in bill_line_pairs:
                    try:
                        bill_service._enqueue_box_uploads(bill, proj_items)
                    except Exception as box_e:
                        logger.warning(f"Could not enqueue Box doc-push for project {proj_id}: {box_e}")
                try:
                    mapping = _box_workbook.read_by_project_id(proj_id)
                    if mapping:
                        entities = [{"entity_type": "bill", "entity_public_id": str(b.public_id)}
                                    for b, _ in bill_line_pairs]
                        if _box_outbox.enqueue_box_excel_batch(
                            entities=entities, project_id=proj_id,
                            box_file_id=mapping["box_file_id"], worksheet_name=mapping["worksheet_name"],
                        ):
                            box_excel_batches += 1
                except Exception as box_e:
                    logger.warning(f"Could not enqueue Box Excel batch for project {proj_id}: {box_e}")

    return {
        "bills_synced": len(bills),
        "bills_module_synced": outcome.projected_count,
        "attachments_synced": attachments_synced,
        "excel_rows_synced": excel_rows_synced,
        "sharepoint_uploads_synced": sharepoint_uploads_synced,
        "box_excel_batches": box_excel_batches,
        "skipped_count": len(outcome.skipped_ids),
        "skipped_bill_ids": outcome.skipped_ids,
        "failed_count": outcome.failed_count,
        # failed_bill_ids: qbo.Bill staging PKs; staging_failed_qbo_ids: QBO API Ids
        "failed_bill_ids": outcome.projection_failed_ids,
        "staging_failed_qbo_ids": outcome.staging_failed_ids,
        "bills": [bill.to_dict() for bill in bills],
    }, outcome


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
    
    # WatermarkRun.last_sync_time is a normalized STRING; ReadSyncs returned datetime objects.
    # Normalize once so mod_dt > cutoff compares datetime-to-datetime (also fixes
    # push_last_sync_time=start_time_str on a fresh DB before WatermarkRun).
    last_sync_cutoff = _parse_datetime(last_sync_time) if last_sync_time else None
    
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
            if mod_dt and last_sync_cutoff and mod_dt > last_sync_cutoff:
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
    pull_only: bool = True,
    dry_run: bool = False,
) -> dict:
    """
    One-way sync for QBO Bills -> Bill module (QBO -> Local only, by default).

    1. QBO -> Local: Fetch bills modified since last sync, store locally, sync to Bill

    Note: Local -> QBO push is disabled by default (pull_only=True).
    The sync_local_to_qbo function is preserved for one-time pushes
    when a Bill is marked Complete. Use --push to explicitly enable
    the push phase if needed.

    Args:
        start_date: Optional start date (YYYY-MM-DD) for filtering bills by TxnDate.
        end_date: Optional end date (YYYY-MM-DD) for filtering bills by TxnDate.
        skip_sync_record_update: If True, don't update the sync record timestamp.
        sync_attachments: If True, also sync attachments for each bill.
        pull_only: If True, skip the push (local -> QBO) phase.
        dry_run: If True, fetch from QBO and report what would be synced without writing anything.
    """
    try:
        # Initialize services
        sync_service = SyncService()
        qbo_bill_service = QboBillService()
        qbo_bill_repo = QboBillRepository()
        bill_connector = BillBillConnector()
        bill_mapping_repo = BillBillRepository()
        auth_service = QboAuthService()
        attachable_service = QboAttachableService() if sync_attachments else None
        
        provider = 'qbo'
        entity = 'bill'
        env = 'prod'

        run = WatermarkRun(sync_service, provider, env, entity).open()
        start_time_str = _normalize_watermark_value(run.query_start)
        logger.info(f"QBO Bill sync triggered at: {start_time_str}")
        
        if start_date or end_date:
            logger.info(f"Date range filter: {start_date or 'beginning'} to {end_date or 'now'}")
        
        # Get realm ID
        all_auths = auth_service.read_all()
        if not all_auths or len(all_auths) == 0:
            raise ValueError("No QBO authentication found. Please connect your QuickBooks account first.")
        realm_id = all_auths[0].realm_id
        logger.info(f"Using realm_id: {realm_id}")
        
        # For date range queries, don't use last_sync_time (we're doing historical batch)
        # For regular incremental sync, use last_sync_time
        last_sync_time = None
        if start_date or end_date:
            logger.info("Historical batch sync mode - using date range filter instead of last sync time")
        elif run.last_sync_time:
            last_sync_time = run.last_sync_time
            logger.info(f"Last sync time: {last_sync_time}. Fetching only updated records.")
        else:
            logger.info("No previous sync found. Performing full sync.")

        # --- DRY RUN path: fetch from QBO only, no DB writes, no QBO writes ---
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

        # Step 1: Sync from QBO to local
        qbo_to_local_result, outcome = sync_qbo_to_local(
            realm_id=realm_id,
            last_sync_time=last_sync_time,
            qbo_bill_service=qbo_bill_service,
            bill_connector=bill_connector,
            start_date=start_date,
            end_date=end_date,
            sync_attachments=sync_attachments,
            attachable_service=attachable_service,
        )
        
        local_to_qbo_result = None
        push_run = None
        if not pull_only:
            # Step 2: Sync from local to QBO (reverse sync)
            push_run = WatermarkRun(sync_service, provider, env, 'bill_push').open()
            
            push_last_sync_time = None
            if push_run.last_sync_time:
                push_last_sync_time = push_run.last_sync_time
                logger.info(f"Push sync - last sync time: {push_last_sync_time}")
            else:
                logger.info("No previous push sync found. Will only sync bills from now onward.")
                push_last_sync_time = start_time_str
            
            local_to_qbo_result = sync_local_to_qbo(
                realm_id=realm_id,
                last_sync_time=push_last_sync_time,
                qbo_bill_service=qbo_bill_service,
                bill_mapping_repo=bill_mapping_repo,
                qbo_bill_repo=qbo_bill_repo,
                sync_attachments=sync_attachments,
            )
        else:
            logger.info("Pull-only mode - skipping push to QBO")
        
        end_time = datetime.now(timezone.utc)
        end_time_str = _normalize_last_sync(end_time.isoformat())

        if push_run is not None:
            push_run.commit_push(
                skip=(pull_only or skip_sync_record_update or local_to_qbo_result is None),
            )

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
            "qbo_to_local": qbo_to_local_result,
            "local_to_qbo": local_to_qbo_result,
        }
        
        pushed_count = local_to_qbo_result['bills_pushed'] if local_to_qbo_result else 0
        logger.info(f"QBO Bill sync completed. Bills from QBO: {qbo_to_local_result['bills_synced']}, "
                    f"Bills module synced: {qbo_to_local_result['bills_module_synced']}, "
                    f"Excel rows synced: {qbo_to_local_result.get('excel_rows_synced', 0)}, "
                    f"SharePoint uploads: {qbo_to_local_result.get('sharepoint_uploads_synced', 0)}, "
                    f"Box Excel batches: {qbo_to_local_result.get('box_excel_batches', 0)}, "
                    f"Bills pushed: {pushed_count}")
        
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
    
    parser.add_argument(
        '--pull-only',
        action='store_true',
        default=True,
        help='Only pull from QBO to local. Skip the push (local -> QBO) phase. This is now the default.'
    )

    parser.add_argument(
        '--push',
        action='store_true',
        help='Explicitly enable push (local -> QBO) phase. Overrides the default pull-only behavior.'
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
    assert_cli_system_admin()
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
    
    # --push overrides the default pull-only behavior
    pull_only = not args.push

    result = sync_qbo_bill(
        start_date=args.start_date,
        end_date=args.end_date,
        skip_sync_record_update=args.skip_sync_update,
        sync_attachments=not args.skip_attachments,
        pull_only=pull_only,
        dry_run=args.dry_run,
    )
    
    import json
    print(json.dumps(result, indent=2, default=str))
