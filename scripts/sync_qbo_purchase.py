# Python Standard Library Imports
import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
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
from integrations.intuit.qbo.purchase.business.service import QboPurchaseService
from integrations.intuit.qbo.purchase.business.model import QboPurchase
from integrations.intuit.qbo.purchase.connector.expense.business.service import (
    PurchaseExpenseConnector,
    sync_purchase_attachments_to_expense_line_items,
)
from integrations.intuit.qbo.attachable.business.service import QboAttachableService
from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseLineRepository
from integrations.intuit.qbo.purchase.external.client import QboPurchaseClient
from integrations.intuit.qbo.auth.business.service import QboAuthService
from entities.expense.business.service import ExpenseService

logger = logging.getLogger(__name__)

# Configure logging for script execution
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Sync configuration
MAX_RETRIES = 3  # Max retries for transient errors
INITIAL_RETRY_DELAY = 2.0  # Initial retry delay (seconds)


def _dry_run_preview(
    realm_id: str,
    last_sync_time: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """
    Dry-run preview: fetch purchases from QBO and report what would be synced
    without writing anything to the local database.
    """
    logger.info("[DRY RUN] Fetching purchases from QBO to preview sync (no writes will occur)...")

    with QboPurchaseClient(realm_id=realm_id) as client:
        qbo_purchases = client.query_all_purchases(
            last_updated_time=last_sync_time,
            start_date=start_date,
            end_date=end_date,
        )

    logger.info(f"[DRY RUN] QBO returned {len(qbo_purchases)} purchases")

    # U-298: classify create-vs-update against dbo.Expense's own native QboId
    # identity (U-283b) — what PurchaseExpenseConnector actually resolves by —
    # instead of qbo.Purchase staging-row existence. qbo.Purchase stays a
    # read-only audit mirror (Chris's 2026-08-20 decision) written on every
    # pull regardless of whether the Expense side landed, so checking IT for
    # this classification can read create/update backwards vs. what the
    # connector will really do (e.g. a staging row surviving an Expense create
    # that failed/rolled back on a prior tick).
    expense_service = ExpenseService()
    existing_expense_qbo_ids = expense_service.read_qbo_ids_by_realm_id(realm_id)

    would_create = [p for p in qbo_purchases if p.id not in existing_expense_qbo_ids]
    would_update = [p for p in qbo_purchases if p.id in existing_expense_qbo_ids]

    logger.info(f"[DRY RUN] dbo.Expense (native identity):")
    logger.info(f"[DRY RUN]   {len(would_create)} would be CREATED")
    logger.info(f"[DRY RUN]   {len(would_update)} would be UPDATED")
    logger.info(f"[DRY RUN] Existing local expenses (this realm): {len(existing_expense_qbo_ids)}")
    logger.info("[DRY RUN] No changes were made to the local database.")

    sample = [
        {"qbo_id": p.id, "doc_number": p.doc_number, "vendor": p.entity_ref.name if p.entity_ref else None, "txn_date": p.txn_date, "total": float(p.total_amt) if p.total_amt else None}
        for p in would_create[:5]
    ]

    return {
        "dry_run": True,
        "direction": "QBO → BuildOne only (read-only from QBO)",
        "qbo_records_found": len(qbo_purchases),
        "expense_identity": {
            "would_create": len(would_create),
            "would_update": len(would_update),
        },
        "local_expenses_existing": len(existing_expense_qbo_ids),
        "sample_new_records": sample,
    }


# A purchase first projected this tick (no local Expense yet — deferred earlier,
# skipped, or pulled late) may carry receipts older than the snapshot bound. A
# receipt cannot predate the purchase it is attached to, and a purchase is not
# entered in QBO before its transaction date (Ramp posts after clearing), so a
# window starting `CREATE_WINDOW_MARGIN_DAYS` before the transaction date holds
# every receipt it can have — exact, and a few days of attachables instead of
# the ~19K-row realm. Beyond the cap (or with no date) the full list is used.
CREATE_WINDOW_MARGIN_DAYS = 3
CREATE_WINDOW_CAP_DAYS = 60


def _attachable_window_for_create(purchase, last_sync_time: Optional[str]) -> tuple:
    """
    (authoritative, window_since) for a purchase that is NEW locally.

    Returns (False, None) on a full pull (the snapshot is already the full
    list); (True, None) when the age is unknown or past the cap; otherwise
    (False, "<ISO date>") — the create-window bound.
    """
    if not last_sync_time:
        return (False, None)
    txn_raw = getattr(purchase, "txn_date", None)
    if not txn_raw:
        return (True, None)
    try:
        txn = datetime.fromisoformat(str(txn_raw)[:10]).date()
        watermark = datetime.fromisoformat(str(last_sync_time).replace("Z", "+00:00")).date()
    except ValueError:
        return (True, None)
    if (watermark - txn).days > CREATE_WINDOW_CAP_DAYS:
        return (True, None)
    since = txn - timedelta(days=CREATE_WINDOW_MARGIN_DAYS)
    return (False, f"{since.isoformat()}T00:00:00+00:00")


def _link_late_attachables(
    *,
    realm_id: str,
    attachable_service: QboAttachableService,
    expense_service: ExpenseService,
    changed_qbo_ids: set,
) -> int:
    """
    Failure-isolated wrapper: a problem linking late receipts must never fail
    the tick (the watermark still commits — continuity is per attachable, and
    the next tick's bound starts from this one's query start, not from here).
    """
    try:
        return _link_attachables_for_unchanged_purchases(
            realm_id=realm_id,
            attachable_service=attachable_service,
            expense_service=expense_service,
            changed_qbo_ids=changed_qbo_ids,
        )
    except (QboBudgetExceededError, QboWriteRefusedError):
        raise
    except Exception as orphan_e:
        logger.warning(f"Could not link late-attached receipts: {orphan_e}")
        return 0


def _link_attachables_for_unchanged_purchases(
    *,
    realm_id: str,
    attachable_service: QboAttachableService,
    expense_service: ExpenseService,
    changed_qbo_ids: set,
) -> int:
    """
    Sync + link attachables whose parent Purchase did NOT change this tick.

    QBO does not bump a Purchase's LastUpdatedTime when a receipt is attached,
    so a Ramp receipt matched after the purchase's own pull never re-enters the
    main loop. Runs on EVERY incremental tick, including one with no changed
    purchases. Returns the number of ExpenseLineItemAttachment links created.
    Purchases with no local Expense (never projected, or skipped) are left
    alone — their own projection, when it comes, looks back to their own
    transaction date (`_attachable_window_for_create`), which covers any
    receipt matched in the meantime.
    """
    touched = attachable_service.entity_ids_in_snapshot(realm_id, "Purchase")
    pending = sorted(touched - set(changed_qbo_ids))
    if not pending:
        return 0
    logger.info(f"Late-attached receipts: {len(pending)} purchase(s) with new attachables but no header change")

    linked_total = 0
    for qbo_id in pending:
        try:
            expense = expense_service.read_by_qbo_identity(qbo_id, realm_id)
            if expense is None:
                continue
            qbo_attachables = attachable_service.sync_attachables_for_purchase(
                realm_id=realm_id, purchase_qbo_id=qbo_id, sync_to_modules=True,
            )
            if not qbo_attachables:
                continue
            expense_id = int(expense.id) if isinstance(expense.id, str) else expense.id
            linked_total += sync_purchase_attachments_to_expense_line_items(
                expense_id=expense_id, qbo_attachables=qbo_attachables,
            )
        except (QboBudgetExceededError, QboWriteRefusedError):
            raise
        except Exception as att_e:
            logger.warning(f"Could not link late-attached receipt for Purchase {qbo_id}: {att_e}")
    return linked_total


def sync_qbo_to_local(
    realm_id: str,
    last_sync_time: Optional[str],
    qbo_purchase_service: QboPurchaseService,
    purchase_connector: PurchaseExpenseConnector,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> tuple[dict, SyncOutcome]:
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
        tuple[dict, SyncOutcome]: Sync results envelope and service pull outcome
    """
    if start_date or end_date:
        logger.info(f"Syncing Purchases from QBO API for realm_id: {realm_id} (TxnDate: {start_date or 'beginning'} to {end_date or 'now'})")
    else:
        logger.info(f"Syncing Purchases from QBO API for realm_id: {realm_id}")
    
    # Fetch purchases from QBO and store locally (without auto-syncing to modules)
    outcome = qbo_purchase_service.sync_from_qbo(
        realm_id=realm_id,
        last_updated_time=last_sync_time,
        start_date=start_date,
        end_date=end_date,
        sync_to_modules=False,  # Module sync handled below for better control
        reconcile_deletes=True,  # Removes local records for purchases deleted in QBO (full syncs only)
    )
    purchases = outcome.synced

    # Created BEFORE the empty-tick early return below: the late-attach pass
    # must run on EVERY incremental tick. The watermark commits on an empty
    # tick too, and the next tick bounds its attachable snapshot by it — so a
    # receipt matched during a quiet window would otherwise never be seen again.
    attachable_service = QboAttachableService(attachables_since=last_sync_time)
    from entities.expense.business.service import ExpenseService
    from entities.expense_line_item.business.service import ExpenseLineItemService
    expense_service = ExpenseService()
    expense_line_item_service = ExpenseLineItemService()
    late_linked = 0
    if last_sync_time:
        late_linked = _link_late_attachables(
            realm_id=realm_id,
            attachable_service=attachable_service,
            expense_service=expense_service,
            changed_qbo_ids={p.qbo_id for p in purchases if p.qbo_id},
        )

    if not purchases:
        logger.info(f"No Purchase updates found since {last_sync_time or 'beginning'}")
        return {
            "purchases_synced": 0,
            "expenses_module_synced": 0,
            "expenses_completed": 0,
            "attachments_linked": late_linked,
            "ms_fanout_enqueued": 0,
            "ms_fanout_refused": 0,
            "box_excel_batches": 0,
            "skipped_count": 0,
            "skipped_purchase_ids": [],
            "failed_count": outcome.failed_count,
            # failed_purchase_ids: qbo.Purchase staging PKs; staging_failed_qbo_ids: QBO API Ids
            "failed_purchase_ids": outcome.projection_failed_ids,
            "staging_failed_qbo_ids": outcome.staging_failed_ids,
            "purchases": [],
        }, outcome
    
    logger.info(f"Retrieved {len(purchases)} purchases from QBO")
    
    # Sync purchases to Expense module
    attachments_linked = late_linked
    ms_fanout_enqueued = 0   # expense_pull_fanout outbox rows written (Excel + SharePoint run at drain)
    ms_fanout_refused = 0    # ALLOW_MS_WRITES gate off / no tenant — nothing enqueued
    box_excel_batches = 0
    synced_expenses = []     # (expense, expense_id) — collected for the per-project fan-out
    # A1: the per-run attachable snapshot is bounded to the pull's own watermark
    # (`attachable_service` above). An incremental tick pages only attachables
    # updated since `last_sync_time` (overlap included) instead of the full
    # ~19K-row realm list; a full or historical pull keeps the full list.

    for i, purchase in enumerate(purchases):
        try:
            # Known BEFORE projection: a purchase with no local Expense yet is a
            # CREATE (deferred earlier, skipped, or pulled late). Its receipts may
            # predate the snapshot bound, so its attachable lookup uses the
            # create window — see `_attachable_window_for_create`.
            was_local = (
                expense_service.read_by_qbo_identity(purchase.qbo_id, realm_id) is not None
                if (last_sync_time and purchase.qbo_id) else True
            )
            authoritative, window_since = (
                _attachable_window_for_create(purchase, last_sync_time) if not was_local else (False, None)
            )
            # Get purchase lines, re-reading to ride out the cross-process pull-race (an empty
            # read colliding with a non-zero header). If the lines never arrive, DEFER the row:
            # skip it WITHOUT failing so the watermark advances — avoids stalling the sync on a
            # genuinely line-less record. NOTE: there is no purchase reconciler yet (only bills
            # are reconciled), so a RARE genuinely-line-less purchase is logged here but not
            # auto-recovered — see TODO.md ("QBO reconcilers for purchase/vendorcredit").
            purchase_lines = read_lines_riding_out_race(
                qbo_purchase_service.read_lines_by_qbo_purchase_id, purchase.id, purchase.total_amt
            )
            if not purchase_lines and header_has_amount(purchase.total_amt):
                logger.warning(
                    f"Deferring QboPurchase {purchase.id} (qbo_id={purchase.qbo_id}): no lines for "
                    f"non-zero header {purchase.total_amt} after re-read (pull race or genuinely "
                    f"line-less); skipping so the watermark advances (no purchase reconciler yet — logged only)."
                )
                continue

            # Use retry logic for transient errors
            expense = with_retry(
                purchase_connector.sync_from_qbo_purchase,
                purchase,
                purchase_lines,
                max_retries=MAX_RETRIES,
                initial_delay=INITIAL_RETRY_DELAY,
            )
            outcome.record_projected()
            expense_id = int(expense.id) if isinstance(expense.id, str) else expense.id
            synced_expenses.append((expense, expense_id))
            logger.info(f"Synced QboPurchase {purchase.id} to Expense {expense.id}")

            # Sync attachables (download to Attachment module) and link to ExpenseLineItems
            if purchase.qbo_id:
                try:
                    qbo_attachables = attachable_service.sync_attachables_for_purchase(
                        realm_id=realm_id,
                        purchase_qbo_id=purchase.qbo_id,
                        sync_to_modules=True,
                        authoritative=authoritative,
                        window_since=window_since,
                    )
                    if qbo_attachables:
                        linked = sync_purchase_attachments_to_expense_line_items(
                            expense_id=expense_id,
                            qbo_attachables=qbo_attachables,
                        )
                        attachments_linked += linked
                except (QboBudgetExceededError, QboWriteRefusedError):
                    raise
                except Exception as att_e:
                    logger.warning(f"Could not sync/link attachments for Purchase {purchase.qbo_id}: {att_e}")

        except Exception as e:
            outcome.record_projection_error(
                purchase.qbo_id, e, label="QboPurchase->Expense", logger=logger
            )

        # Add delay between batches to keep connection alive
        pace_batch(i, len(purchases), logger, "purchases")

    # --- MS fan-out (Excel DETAILS rows + SharePoint module-folder upload) ---
    # A2: enqueued, never run inline. Each (expense, project) pair becomes one
    # `expense_pull_fanout` outbox row; the drain handler re-reads the expense
    # and runs the same ExpenseService methods this tick used to call directly
    # (folder resolution, workbook session, used-range read, blob probes — all
    # MS Graph HTTP that used to sit inside the pull and stretch it past the
    # next timer). Box doc-push and Box Excel were already enqueue-only.
    if synced_expenses:
        # Build project_id -> [(expense, [line_items]), ...] mapping
        project_expense_map = {}  # project_id -> [(expense, [line_items_for_this_project])]
        expense_line_counts = {}  # expense.id -> total line count (SharePoint filename parity with completion)
        for expense, expense_id in synced_expenses:
            try:
                eli_list = expense_line_item_service.read_by_expense_id(expense_id=expense_id)
                expense_line_counts[expense.id] = len(eli_list)
                by_project = {}
                for eli in eli_list:
                    if eli.project_id:
                        by_project.setdefault(eli.project_id, []).append(eli)
                for proj_id, proj_items in by_project.items():
                    project_expense_map.setdefault(proj_id, []).append((expense, proj_items))
            except Exception as e:
                logger.warning(f"Could not read line items for Expense {expense_id} for Excel sync: {e}")

        if project_expense_map:
            logger.info(f"MS fan-out: {len(project_expense_map)} project(s) across {len(synced_expenses)} expense(s) — enqueueing")
        from integrations.ms.outbox.business.service import MsOutboxService
        ms_outbox = MsOutboxService()
        for proj_id, expense_line_pairs in project_expense_map.items():
            for expense, proj_items in expense_line_pairs:
                try:
                    queued = ms_outbox.enqueue_expense_pull_fanout(
                        expense_public_id=str(expense.public_id),
                        project_id=proj_id,
                        expense_line_items_count=expense_line_counts.get(expense.id, len(proj_items)),
                    )
                    if queued is None:
                        ms_fanout_refused += 1
                    else:
                        ms_fanout_enqueued += 1
                except Exception as fan_e:
                    logger.warning(f"Could not enqueue MS fan-out for Expense {expense.public_id} / project {proj_id}: {fan_e}")

        # --- Box: doc-push (PDFs -> project's "14 - Invoices") + BATCHED Box Excel ---
        # Best-effort, ALLOW_BOX_WRITES-gated. Box Excel is batched per project (one
        # download/edit/upload per workbook for all the project's pulled expenses).
        from shared.env_flags import env_flag_enabled
        if env_flag_enabled("ALLOW_BOX_WRITES"):
            from integrations.box.outbox.business.service import BoxOutboxService
            from integrations.box.excel.business.mapping_service import BoxProjectWorkbookService
            _box_outbox = BoxOutboxService()
            _box_workbook = BoxProjectWorkbookService()
            for proj_id, expense_line_pairs in project_expense_map.items():
                for expense, proj_items in expense_line_pairs:
                    try:
                        expense_service._enqueue_box_uploads(expense, proj_items, doc_kind="attachment")
                    except Exception as box_e:
                        logger.warning(f"Could not enqueue Box doc-push for project {proj_id}: {box_e}")
                try:
                    mapping = _box_workbook.read_by_project_id(proj_id)
                    if mapping:
                        entities = [{"entity_type": "expense", "entity_public_id": str(e.public_id)}
                                    for e, _ in expense_line_pairs]
                        if _box_outbox.enqueue_box_excel_batch(
                            entities=entities, project_id=proj_id,
                            box_file_id=mapping["box_file_id"], worksheet_name=mapping["worksheet_name"],
                        ):
                            box_excel_batches += 1
                except Exception as box_e:
                    logger.warning(f"Could not enqueue Box Excel batch for project {proj_id}: {box_e}")

    # Auto-complete intentionally removed (QBO-pull Step 7 cleanup). The projection
    # lands status='completed' / origin qbo_pull, so pulled expenses already arrive
    # final. Calling complete_expense() here would (a) push the expense BACK to QBO
    # — circular, since it came from QBO — and (b) double every doc/tracker side-
    # effect this pull now drives directly (SharePoint / MS-Excel / Box, Steps 4-6).
    # The old loop always short-circuited on `not exp.is_draft`, so removing it is
    # behavior-identical. Kept at 0 for the result-dict contract.
    expenses_completed = 0

    return {
        "purchases_synced": len(purchases),
        "expenses_module_synced": outcome.projected_count,
        "expenses_completed": expenses_completed,
        "attachments_linked": attachments_linked,
        "ms_fanout_enqueued": ms_fanout_enqueued,
        "ms_fanout_refused": ms_fanout_refused,
        "box_excel_batches": box_excel_batches,
        "skipped_count": len(outcome.skipped_ids),
        "skipped_purchase_ids": outcome.skipped_ids,
        "failed_count": outcome.failed_count,
        # failed_purchase_ids: qbo.Purchase staging PKs; staging_failed_qbo_ids: QBO API Ids
        "failed_purchase_ids": outcome.projection_failed_ids,
        "staging_failed_qbo_ids": outcome.staging_failed_ids,
        "purchases": [purchase.to_dict() for purchase in purchases],
    }, outcome


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
        sync_service = SyncService()
        qbo_purchase_service = QboPurchaseService()
        purchase_connector = PurchaseExpenseConnector()
        auth_service = QboAuthService()

        provider = 'qbo'
        entity = 'purchase'
        env = 'prod'

        run = WatermarkRun(sync_service, provider, env, entity).open()
        start_time_str = _normalize_watermark_value(run.query_start)
        logger.info(f"QBO Purchase sync triggered at: {start_time_str}")

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
            qbo_purchase_service=qbo_purchase_service,
            purchase_connector=purchase_connector,
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
            "qbo_to_local": qbo_to_local_result,
        }
        
        logger.info(f"QBO Purchase sync completed. Purchases from QBO: {qbo_to_local_result['purchases_synced']}, "
                    f"Expenses synced: {qbo_to_local_result['expenses_module_synced']}, "
                    f"Skipped: {qbo_to_local_result.get('skipped_count', 0)}, "
                    f"Failed: {qbo_to_local_result.get('failed_count', 0)}, "
                    f"Attachments linked: {qbo_to_local_result['attachments_linked']}, "
                    f"MS fan-out enqueued: {qbo_to_local_result['ms_fanout_enqueued']} "
                    f"(refused: {qbo_to_local_result['ms_fanout_refused']}), "
                    f"Box Excel batches: {qbo_to_local_result['box_excel_batches']}")
        
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


@qbo_sync_locked_cli("purchase")
def run_locked(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    skip_sync_record_update: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Lock-wrapped entry point for a direct CLI run (`python scripts/sync_qbo_purchase.py`).

    This CLI invocation is a third path onto QboPurchaseService.sync_from_qbo,
    independent of the (already-locked) admin dispatcher — locking must live
    at this outer layer, not inside `sync_qbo_purchase()`, which the admin
    path also calls while already holding this same resource (see
    scripts/sync_qbo_account.py::run_locked for the full rationale).
    """
    return sync_qbo_purchase(
        start_date=start_date,
        end_date=end_date,
        skip_sync_record_update=skip_sync_record_update,
        dry_run=dry_run,
    )


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Sync QBO Purchases to BuildOne Expense module',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
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

{END_DATE_CLAMP_EPILOG_NOTE}
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
        dry_run=args.dry_run,
    )
    
    import json
    print(json.dumps(result, indent=2, default=str))
    exit_nonzero_on_sync_failure(result)
