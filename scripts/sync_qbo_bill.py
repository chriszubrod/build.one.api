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
    END_DATE_CLAMP_EPILOG_NOTE,
    WatermarkRun,
    _normalize_last_sync,
    _normalize_watermark_value,
    assert_cli_system_admin,
    exit_nonzero_on_sync_failure,
)
from integrations.intuit.qbo.base.errors import QboBudgetExceededError, QboWriteRefusedError
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
    logger.info("[DRY RUN] This script is pull-only (QBO → BuildOne); local → QBO push was retired (U-218b).")

    sample = [
        {"qbo_id": b.id, "doc_number": b.doc_number, "vendor": b.vendor_ref.name if b.vendor_ref else None, "txn_date": b.txn_date, "total": float(b.total_amt) if b.total_amt else None}
        for b in would_create[:5]
    ]

    return {
        "dry_run": True,
        "direction": "QBO → BuildOne only (pull-only by construction since U-218b)",
        "qbo_records_found": len(qbo_bills),
        "qbo_staging": {
            "would_create": len(would_create),
            "would_update": len(would_update),
        },
        "bill_module_mappings_existing": mapped_count,
        "sample_new_records": sample,
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
    *,
    include_bill_payload: bool = False,
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
                except QboBudgetExceededError:
                    # U-211: environmental refusal — propagate so the pull holds the
                    # watermark instead of reporting complete with attachments dropped.
                    raise
                except QboWriteRefusedError:
                    # U-218e: local write gate — same category as budget; propagate
                    # so the pull holds instead of advancing past a refused month.
                    raise
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
        from shared.env_flags import env_flag_enabled
        if env_flag_enabled("ALLOW_BOX_WRITES"):
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
        "bills": [bill.to_dict() for bill in bills] if include_bill_payload else [],
    }, outcome




def sync_qbo_bill(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    skip_sync_record_update: bool = False,
    sync_attachments: bool = True,
    dry_run: bool = False,
    *,
    include_bill_payload: bool = False,
) -> dict:
    """
    One-way sync for QBO Bills -> Bill module (QBO -> Local only).

    1. QBO -> Local: Fetch bills modified since last sync, store locally, sync to Bill

    Local -> QBO push was retired (U-218b). Use bill completion or
    POST /sync/bill-to-qbo to enqueue via the outbox.

    Args:
        start_date: Optional start date (YYYY-MM-DD) for filtering bills by TxnDate.
        end_date: Optional end date (YYYY-MM-DD) for filtering bills by TxnDate.
        skip_sync_record_update: If True, don't update the sync record timestamp.
        sync_attachments: If True, also sync attachments for each bill.
        dry_run: If True, fetch from QBO and report what would be synced without writing anything.
    """
    try:
        # Initialize services
        sync_service = SyncService()
        qbo_bill_service = QboBillService()
        qbo_bill_repo = QboBillRepository()
        bill_connector = BillBillConnector()
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
        realm_id = auth_service.resolve_realm_id()
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
            preview = _dry_run_preview(
                realm_id=realm_id,
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
            include_bill_payload=include_bill_payload,
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
            "qbo_to_local": qbo_to_local_result,
        }
        
        logger.info(f"QBO Bill sync completed. Bills from QBO: {qbo_to_local_result['bills_synced']}, "
                    f"Bills module synced: {qbo_to_local_result['bills_module_synced']}, "
                    f"Excel rows synced: {qbo_to_local_result.get('excel_rows_synced', 0)}, "
                    f"SharePoint uploads: {qbo_to_local_result.get('sharepoint_uploads_synced', 0)}, "
                    f"Box Excel batches: {qbo_to_local_result.get('box_excel_batches', 0)}")
        
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
        epilog=f"""
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

{END_DATE_CLAMP_EPILOG_NOTE}
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
    
    result = sync_qbo_bill(
        start_date=args.start_date,
        end_date=args.end_date,
        skip_sync_record_update=args.skip_sync_update,
        sync_attachments=not args.skip_attachments,
        dry_run=args.dry_run,
        include_bill_payload=True,
    )

    import json
    print(json.dumps(result, indent=2, default=str))
    exit_nonzero_on_sync_failure(result)
