# Python Standard Library Imports
import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Third-party Imports

# Local Imports
from scripts.sync_helper import (
    END_DATE_CLAMP_EPILOG_NOTE,
    assert_cli_system_admin,
    exit_nonzero_on_sync_failure,
)
from integrations.intuit.qbo.base.locking import qbo_sync_locked_cli
from integrations.intuit.qbo.base.watermark import (
    WatermarkRun,
    _normalize_last_sync,
    _normalize_watermark_value,
)
from integrations.intuit.qbo.base.pacing import pace_batch
from integrations.intuit.qbo.base.errors import QboBudgetExceededError, QboWriteRefusedError
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from shared.database import with_retry
from integrations.intuit.qbo.base.pull_race import read_lines_riding_out_race, header_has_amount
from integrations.sync.business.service import SyncService
from integrations.intuit.qbo.vendorcredit.business.service import QboVendorCreditService
from integrations.intuit.qbo.vendorcredit.business.model import QboVendorCredit
from integrations.intuit.qbo.vendorcredit.external.client import QboVendorCreditClient
from integrations.intuit.qbo.vendorcredit.persistence.repo import QboVendorCreditRepository
from integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service import VendorCreditBillCreditConnector
from integrations.intuit.qbo.auth.business.service import QboAuthService
from integrations.intuit.qbo.attachable.business.service import QboAttachableService
from entities.bill_credit_line_item.business.service import BillCreditLineItemService
from entities.bill_credit.business.complete_service import BillCreditCompleteService
from entities.attachment.business.service import AttachmentService
from entities.bill_credit_line_item_attachment.business.service import BillCreditLineItemAttachmentService

logger = logging.getLogger(__name__)

# Configure logging for script execution
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Sync configuration
MAX_RETRIES = 3  # Max retries for transient errors
INITIAL_RETRY_DELAY = 2.0  # Initial retry delay (seconds)


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

    line_items = bill_credit_line_item_service.read_by_bill_credit_id(bill_credit_id=bill_credit_id)
    if not line_items:
        logger.debug(f"No BillCreditLineItems found for BillCredit {bill_credit_id}")
        return 0

    links_created = 0

    # Pre-load existing links once, then track within-run links in the same set —
    # avoids an N+1 re-query (each per-line read also re-resolved public_id->id) on
    # every (attachment x line item) iteration.
    linked_public_ids = {
        a.bill_credit_line_item_public_id
        for a in bill_credit_line_item_attachment_service.read_by_bill_credit_line_item_ids(
            [li.public_id for li in line_items if li.public_id]
        )
    }

    for qbo_attachable in qbo_attachables:
        # U-300b (pull-side repoint) made the local dbo.Attachment.QboId identity
        # the sole source of truth for every attachable this loop ever sees — the
        # qbo.AttachableAttachment mapping-table fallback U-279 added here is
        # confirmed dead (U-315) and was removed; see TODO.md "U-300b follow-ups".
        attachment = None
        if qbo_attachable.qbo_id:
            attachment = attachment_service.read_by_qbo_identity(qbo_attachable.qbo_id, qbo_attachable.realm_id)
        if not attachment or not attachment.public_id:
            logger.debug(f"Attachment not found for QboAttachable qbo_id={qbo_attachable.qbo_id}")
            continue

        # BillCreditLineItemAttachment is 1:1 — each line item holds at most one attachment.
        # Link this attachment to any line items not yet linked; pre-check existing so a
        # real failure isn't silently swallowed as "already linked".
        attachment_linked_count = 0
        for line_item in line_items:
            if not line_item.public_id or line_item.public_id in linked_public_ids:
                continue
            try:
                bill_credit_line_item_attachment_service.create(
                    bill_credit_line_item_public_id=line_item.public_id,
                    attachment_public_id=attachment.public_id,
                )
                links_created += 1
                attachment_linked_count += 1
                linked_public_ids.add(line_item.public_id)
                logger.debug(f"Linked Attachment {attachment.id} to BillCreditLineItem {line_item.id}")
            except Exception as e:
                logger.debug(f"Could not link Attachment {attachment.id} to BillCreditLineItem {line_item.id}: {e}")
        if attachment_linked_count == 0:
            logger.warning(
                f"BillCredit {bill_credit_id}: Attachment {attachment.id} (QboAttachable {qbo_attachable.id}) "
                f"could not be linked — all {len(line_items)} line item(s) already have an attachment. "
                f"BillCreditLineItemAttachment is 1:1; this attachment is unlinked."
            )

    if links_created > 0:
        logger.info(f"Created {links_created} BillCreditLineItemAttachment links for BillCredit {bill_credit_id}")

    return links_created


def _dry_run_preview(
    realm_id: str,
    last_sync_time: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """
    Dry-run preview: fetch vendor credits from QBO and report what would be synced
    without writing anything to the local database or pushing to QBO.
    """
    logger.info("[DRY RUN] Fetching vendor credits from QBO to preview sync (no writes will occur)...")

    with QboVendorCreditClient(realm_id=realm_id) as client:
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
    sync_attachments: bool = False,
    attachable_service: Optional[QboAttachableService] = None,
) -> tuple[dict, SyncOutcome]:
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
        tuple[dict, SyncOutcome]: Sync results envelope and service pull outcome
    """
    if start_date or end_date:
        logger.info(f"Syncing VendorCredits from QBO API for realm_id: {realm_id} (TxnDate: {start_date or 'beginning'} to {end_date or 'now'})")
    else:
        logger.info(f"Syncing VendorCredits from QBO API for realm_id: {realm_id}")
    
    # Fetch vendor credits from QBO and store locally (without auto-syncing to modules)
    outcome = qbo_vendor_credit_service.sync_from_qbo(
        realm_id=realm_id,
        last_updated_time=last_sync_time,
        start_date=start_date,
        end_date=end_date,
        sync_to_modules=False,  # We'll handle module sync separately for better control
        reconcile_deletes=True,  # full-sync-only guard inside: removes local records deleted in QBO
    )
    vendor_credits = outcome.synced
    
    if not vendor_credits:
        logger.info(f"No VendorCredit updates found since {last_sync_time or 'beginning'}")
        return {
            "vendor_credits_synced": 0,
            "bill_credits_module_synced": 0,
            "attachments_synced": 0,
            "excel_rows_synced": 0,
            "sharepoint_uploads_synced": 0,
            "box_excel_batches": 0,
            "vendor_credits": [],
            "skipped_count": 0,
            "skipped_vendor_credit_ids": [],
            "failed_count": outcome.failed_count,
            # failed_vendor_credit_ids: qbo.VendorCredit staging PKs; staging_failed_qbo_ids: QBO API Ids
            "failed_vendor_credit_ids": outcome.projection_failed_ids,
            "staging_failed_qbo_ids": outcome.staging_failed_ids,
        }, outcome
    
    logger.info(f"Retrieved {len(vendor_credits)} vendor credits from QBO")
    
    # Sync vendor credits to BillCredit module
    attachments_synced = 0
    excel_rows_synced = 0
    sharepoint_uploads_synced = 0
    box_excel_batches = 0
    synced_credits = []    # (bill_credit, bill_credit_id) — collected for batched budget-tracker Excel sync
    bill_credit_complete_service = BillCreditCompleteService()
    bill_credit_line_item_service = BillCreditLineItemService()

    for i, vendor_credit in enumerate(vendor_credits):
        try:
            # Get vendor credit lines, re-reading to ride out the cross-process pull-race (an
            # empty read colliding with a non-zero header). If the lines never arrive, DEFER the
            # row: skip it WITHOUT failing so the watermark advances — avoids stalling the sync on
            # a genuinely line-less record. NOTE: there is no vendorcredit reconciler yet (only
            # bills are reconciled), so a RARE genuinely-line-less credit is logged here but not
            # auto-recovered — see TODO.md ("QBO reconcilers for purchase/vendorcredit").
            vendor_credit_lines = read_lines_riding_out_race(
                qbo_vendor_credit_service.read_lines_by_vendor_credit_id, vendor_credit.id, vendor_credit.total_amt
            )
            if not vendor_credit_lines and header_has_amount(vendor_credit.total_amt):
                logger.warning(
                    f"Deferring QboVendorCredit {vendor_credit.id} (qbo_id={vendor_credit.qbo_id}): no "
                    f"lines for non-zero header {vendor_credit.total_amt} after re-read (pull race or "
                    f"genuinely line-less); skipping so the watermark advances (no vendorcredit reconciler yet — logged only)."
                )
                continue

            # Use retry logic for transient errors
            bill_credit = with_retry(
                vendor_credit_connector.sync_from_qbo_vendor_credit,
                vendor_credit,
                vendor_credit_lines,
                max_retries=MAX_RETRIES,
                initial_delay=INITIAL_RETRY_DELAY,
            )
            if bill_credit:
                outcome.record_projected()
                logger.info(f"Synced QboVendorCredit {vendor_credit.id} to BillCredit {bill_credit.id}")
                # Collect for batched budget-tracker Excel sync after the loop
                synced_credits.append((bill_credit, bill_credit.id))

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
                    except (QboBudgetExceededError, QboWriteRefusedError):
                        raise
                    except Exception as att_e:
                        logger.error(f"Failed to sync attachments for VendorCredit {vendor_credit.qbo_id}: {att_e}")
            else:
                # Bucket contract: every fetched record → synced / projection_failed / skipped.
                logger.error(
                    f"QboVendorCredit {vendor_credit.id}: projection returned no BillCredit row"
                )
                outcome.record_projection_failure(
                    vendor_credit.qbo_id,
                    "sync_from_qbo_vendor_credit returned no BillCredit row",
                )

        except Exception as e:
            outcome.record_projection_error(
                vendor_credit.qbo_id, e, label="QboVendorCredit->BillCredit", logger=logger
            )

        # Add delay between batches to keep connection alive
        pace_batch(i, len(vendor_credits), logger, "vendor credits")

    # --- Batch budget-tracker Excel sync: one worksheet read + batched insert per project ---
    # Mirrors the purchase pull (sync_expenses_batch_to_excel). Best-effort: an Excel
    # failure is logged and never blocks the credit watermark.
    if synced_credits:
        project_credit_map = {}  # project_id -> [(bill_credit, [line_items_for_this_project])]
        for bill_credit, bill_credit_id in synced_credits:
            try:
                bclis = bill_credit_line_item_service.read_by_bill_credit_id(bill_credit_id=bill_credit_id)
                by_project = {}
                for bcli in bclis:
                    if bcli.project_id:
                        by_project.setdefault(bcli.project_id, []).append(bcli)
                for proj_id, proj_items in by_project.items():
                    project_credit_map.setdefault(proj_id, []).append((bill_credit, proj_items))
            except Exception as e:
                logger.warning(f"Could not read line items for BillCredit {bill_credit_id} for Excel sync: {e}")

        if project_credit_map:
            logger.info(f"Excel sync: {len(project_credit_map)} project(s) to sync across {len(synced_credits)} credit(s)")
        for proj_id, bill_credit_line_pairs in project_credit_map.items():
            try:
                excel_result = bill_credit_complete_service.sync_bill_credits_batch_to_excel(
                    bill_credit_line_pairs=bill_credit_line_pairs,
                    project_id=proj_id,
                )
                excel_rows_synced += excel_result.get("synced_count", 0)
                if excel_result.get("errors"):
                    for err in excel_result["errors"]:
                        logger.warning(f"Excel sync error for project {proj_id}: {err}")
            except Exception as excel_e:
                logger.warning(f"Could not sync bill credits to Excel for project {proj_id}: {excel_e}")

        # --- SharePoint document upload (best-effort) ---
        # Re-pull-safe without a synced-guard: incremental watermark avoids re-fetching an
        # unchanged credit; a re-pull uses conflictBehavior=replace (refresh, not duplicate).
        for proj_id, bill_credit_line_pairs in project_credit_map.items():
            for bill_credit, proj_items in bill_credit_line_pairs:
                try:
                    sp_result = bill_credit_complete_service._upload_attachments_to_module_folder(
                        bill_credit=bill_credit,
                        line_items=proj_items,
                        project_id=proj_id,
                    )
                    sharepoint_uploads_synced += sp_result.get("synced_count", 0)
                    if sp_result.get("errors"):
                        for err in sp_result["errors"]:
                            logger.warning(f"SharePoint upload error for project {proj_id}: {err}")
                except Exception as sp_e:
                    logger.warning(f"Could not upload credit attachments to SharePoint for project {proj_id}: {sp_e}")

        # --- Box: doc-push (PDFs -> project's "14 - Invoices") + BATCHED Box Excel ---
        # Best-effort, ALLOW_BOX_WRITES-gated. Box Excel is batched per project (one
        # download/edit/upload per workbook for all the project's pulled credits).
        from shared.env_flags import env_flag_enabled
        if env_flag_enabled("ALLOW_BOX_WRITES"):
            from integrations.box.outbox.business.service import BoxOutboxService
            from integrations.box.excel.business.mapping_service import BoxProjectWorkbookService
            _box_outbox = BoxOutboxService()
            _box_workbook = BoxProjectWorkbookService()
            for proj_id, bill_credit_line_pairs in project_credit_map.items():
                for bill_credit, proj_items in bill_credit_line_pairs:
                    try:
                        bill_credit_complete_service._enqueue_box_uploads(bill_credit, proj_items)
                    except Exception as box_e:
                        logger.warning(f"Could not enqueue Box doc-push for project {proj_id}: {box_e}")
                try:
                    mapping = _box_workbook.read_by_project_id(proj_id)
                    if mapping:
                        entities = [{"entity_type": "bill_credit", "entity_public_id": str(c.public_id)}
                                    for c, _ in bill_credit_line_pairs]
                        if _box_outbox.enqueue_box_excel_batch(
                            entities=entities, project_id=proj_id,
                            box_file_id=mapping["box_file_id"], worksheet_name=mapping["worksheet_name"],
                        ):
                            box_excel_batches += 1
                except Exception as box_e:
                    logger.warning(f"Could not enqueue Box Excel batch for project {proj_id}: {box_e}")

    return {
        "vendor_credits_synced": len(vendor_credits),
        "bill_credits_module_synced": outcome.projected_count,
        "attachments_synced": attachments_synced,
        "excel_rows_synced": excel_rows_synced,
        "sharepoint_uploads_synced": sharepoint_uploads_synced,
        "box_excel_batches": box_excel_batches,
        "skipped_count": len(outcome.skipped_ids),
        "skipped_vendor_credit_ids": outcome.skipped_ids,
        "failed_count": outcome.failed_count,
        # failed_vendor_credit_ids: qbo.VendorCredit staging PKs; staging_failed_qbo_ids: QBO API Ids
        "failed_vendor_credit_ids": outcome.projection_failed_ids,
        "staging_failed_qbo_ids": outcome.staging_failed_ids,
        "vendor_credits": [vc.to_dict() for vc in vendor_credits],
    }, outcome


def _attachment_kill_switch_engaged() -> bool:
    """Env kill switch for the vendor-credit attachment pull (U-219).

    This pull path had never executed in production before U-219 wired it (the
    attachable service was built but never passed in), and BOTH recurring callers
    -- the APScheduler fallback and the scheduler Function's POST -- take the
    `sync_attachments=True` default, so the CLI's `--skip-attachments` cannot reach
    them. Setting `QBO_VENDORCREDIT_SYNC_ATTACHMENTS=false` in App Service settings
    turns the pull off with a restart instead of a code edit + redeploy.

    Deliberately one-way: it can only DISABLE. An explicit caller asking for
    attachments is still refused when the switch is set, but the switch never
    turns attachments ON for a caller that asked to skip them.
    """
    return os.getenv("QBO_VENDORCREDIT_SYNC_ATTACHMENTS", "true").strip().lower() == "false"


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
        if sync_attachments and _attachment_kill_switch_engaged():
            logger.warning(
                "QBO_VENDORCREDIT_SYNC_ATTACHMENTS=false — skipping vendor-credit "
                "attachment sync for this run (env kill switch)."
            )
            sync_attachments = False

        sync_service = SyncService()
        qbo_vendor_credit_service = QboVendorCreditService()
        vendor_credit_connector = VendorCreditBillCreditConnector()
        auth_service = QboAuthService()
        attachable_service = QboAttachableService() if sync_attachments else None
        
        provider = 'qbo'
        entity = 'vendorcredit'
        env = 'prod'

        run = WatermarkRun(sync_service, provider, env, entity).open()
        start_time_str = _normalize_watermark_value(run.query_start)
        logger.info(f"QBO VendorCredit sync triggered at: {start_time_str}")
        
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

        # --- DRY RUN path: fetch from QBO only, no DB writes ---
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

        qbo_to_local_result, outcome = sync_qbo_to_local(
            realm_id=realm_id,
            last_sync_time=last_sync_time,
            qbo_vendor_credit_service=qbo_vendor_credit_service,
            vendor_credit_connector=vendor_credit_connector,
            start_date=start_date,
            end_date=end_date,
            sync_attachments=sync_attachments,
            attachable_service=attachable_service,
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
        
        logger.info(
            f"QBO VendorCredit sync completed. VendorCredits from QBO: {qbo_to_local_result['vendor_credits_synced']}, "
            f"BillCredits module synced: {qbo_to_local_result['bill_credits_module_synced']}, "
            f"attachments synced: {qbo_to_local_result['attachments_synced']}, "
            f"Excel rows synced: {qbo_to_local_result['excel_rows_synced']}, "
            f"SharePoint uploads: {qbo_to_local_result['sharepoint_uploads_synced']}, "
            f"Box Excel batches: {qbo_to_local_result['box_excel_batches']}"
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


@qbo_sync_locked_cli("vendorcredit")
def run_locked(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    skip_sync_record_update: bool = False,
    sync_attachments: bool = True,
    dry_run: bool = False,
) -> dict:
    """
    Lock-wrapped entry point for a direct CLI run (`python scripts/sync_qbo_vendorcredit.py`).

    This CLI invocation is a third path onto QboVendorCreditService.sync_from_qbo,
    independent of the (already-locked) admin dispatcher — locking must live
    at this outer layer, not inside `sync_qbo_vendorcredit()`, which the admin
    path also calls while already holding this same resource (see
    scripts/sync_qbo_account.py::run_locked for the full rationale).
    """
    return sync_qbo_vendorcredit(
        start_date=start_date,
        end_date=end_date,
        skip_sync_record_update=skip_sync_record_update,
        sync_attachments=sync_attachments,
        dry_run=dry_run,
    )


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Sync QBO VendorCredits to BuildOne BillCredit module',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
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

{END_DATE_CLAMP_EPILOG_NOTE}
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
    
    result = run_locked(
        start_date=args.start_date,
        end_date=args.end_date,
        skip_sync_record_update=args.skip_sync_update,
        sync_attachments=not args.skip_attachments,
        dry_run=args.dry_run,
    )
    
    import json
    print(json.dumps(result, indent=2, default=str))
    exit_nonzero_on_sync_failure(result)
