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
from integrations.intuit.qbo.base.locking import qbo_app_lock, qbo_entity_sync_lock_resource
from integrations.intuit.qbo.base.pacing import pace_batch
from integrations.intuit.qbo.base.watermark import (
    WatermarkRun,
    _normalize_last_sync,
    _normalize_watermark_value,
)
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from shared.database import with_retry
from integrations.sync.business.service import SyncService
from integrations.intuit.qbo.invoice.business.service import QboInvoiceService
from integrations.intuit.qbo.invoice.business.model import QboInvoice
from integrations.intuit.qbo.invoice.external.client import QboInvoiceClient
from integrations.intuit.qbo.invoice.persistence.repo import QboInvoiceRepository
from integrations.intuit.qbo.invoice.connector.invoice.business.service import InvoiceInvoiceConnector
from integrations.intuit.qbo.invoice.connector.invoice.persistence.repo import InvoiceInvoiceRepository
from integrations.intuit.qbo.auth.business.service import QboAuthService
from entities.project.business.service import ProjectService

logger = logging.getLogger(__name__)

# Configure logging for script execution
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Sync configuration
MAX_RETRIES = 3  # Max retries for transient errors
INITIAL_RETRY_DELAY = 2.0  # Initial retry delay (seconds)


def _resolve_project_to_customer_ref(project_name: str) -> str:
    """
    Resolve a project name to its QBO Customer ID.

    Looks up: Project -> dbo.Project.QboId directly (no qbo.CustomerProject
    mapping-table hop — Wave-5 U-312).

    Args:
        project_name: Project name (or partial name) to search for

    Returns:
        str: QBO Customer ID

    Raises:
        ValueError: If project or QBO identity not found
    """
    project_service = ProjectService()
    all_projects = project_service.read_all()

    matches = [p for p in all_projects if project_name.lower() in (p.name or "").lower()]
    if not matches:
        raise ValueError(f"No project found matching '{project_name}'")
    if len(matches) > 1:
        names = [p.name for p in matches]
        raise ValueError(f"Multiple projects match '{project_name}': {names}")

    matched = matches[0]
    logger.info(f"Found project: {matched.name} (id={matched.id})")

    # ReadProjects (read_all's sproc) doesn't select QboId/RealmId (base-file
    # gap, TODO.md) — re-fetch by id via ReadProjectById, which does.
    project = project_service.read_by_id(matched.id)
    if not project or not project.qbo_id:
        raise ValueError(f"No QBO identity (QboId) found for project '{matched.name}' (id={matched.id})")

    logger.info(f"Resolved project '{project.name}' to QBO Customer {project.qbo_id}")
    return project.qbo_id


def _dry_run_preview(
    realm_id: str,
    last_sync_time: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    customer_ref: Optional[str] = None,
) -> dict:
    """
    Dry-run preview: fetch invoices from QBO and report what would be synced
    without writing anything to the local database or pushing to QBO.
    """
    logger.info("[DRY RUN] Fetching invoices from QBO to preview sync (no writes will occur)...")

    with QboInvoiceClient(realm_id=realm_id) as client:
        qbo_invoices = client.query_all_invoices(
            last_updated_time=last_sync_time,
            start_date=start_date,
            end_date=end_date,
            customer_ref=customer_ref,
        )

    logger.info(f"[DRY RUN] QBO returned {len(qbo_invoices)} invoices")

    # Check existing local QBO invoice records (read-only)
    invoice_repo = QboInvoiceRepository()
    existing = invoice_repo.read_by_realm_id(realm_id)
    existing_qbo_ids = {inv.qbo_id for inv in existing}

    # Check existing Invoice module mappings (read-only)
    mapping_repo = InvoiceInvoiceRepository()
    mapped_invoice_ids = mapping_repo.read_all_invoice_ids()

    would_create_qbo = [inv for inv in qbo_invoices if inv.id not in existing_qbo_ids]
    would_update_qbo = [inv for inv in qbo_invoices if inv.id in existing_qbo_ids]

    logger.info(f"[DRY RUN] QBO staging table (qbo.Invoice):")
    logger.info(f"[DRY RUN]   {len(would_create_qbo)} would be CREATED")
    logger.info(f"[DRY RUN]   {len(would_update_qbo)} would be UPDATED")
    logger.info(f"[DRY RUN] Invoice module mappings already in place: {len(mapped_invoice_ids)}")
    logger.info("[DRY RUN] No changes were made to the local database.")
    logger.info("[DRY RUN] No data was pushed to QBO.")

    # Sample of records that would be created
    sample = [
        {"qbo_id": inv.id, "doc_number": inv.doc_number, "customer": inv.customer_ref.name if inv.customer_ref else None, "txn_date": inv.txn_date, "total": float(inv.total_amt) if inv.total_amt else None}
        for inv in would_create_qbo[:5]
    ]

    return {
        "dry_run": True,
        "direction": "QBO → BuildOne only (read-only from QBO)",
        "qbo_records_found": len(qbo_invoices),
        "qbo_staging": {
            "would_create": len(would_create_qbo),
            "would_update": len(would_update_qbo),
        },
        "invoice_module_mappings_existing": len(mapped_invoice_ids),
        "sample_new_records": sample,
    }


def sync_qbo_to_local(
    realm_id: str,
    last_sync_time: Optional[str],
    qbo_invoice_service: QboInvoiceService,
    invoice_connector: InvoiceInvoiceConnector,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    customer_ref: Optional[str] = None,
) -> tuple[dict, SyncOutcome]:
    """
    Sync Invoices from QBO API to local database and modules.
    
    Args:
        realm_id: QBO realm ID
        last_sync_time: Last sync timestamp for incremental sync
        qbo_invoice_service: QboInvoiceService instance
        invoice_connector: InvoiceInvoiceConnector instance
        start_date: Optional start date (YYYY-MM-DD) for filtering by TxnDate
        end_date: Optional end date (YYYY-MM-DD) for filtering by TxnDate
        customer_ref: Optional QBO Customer ID for filtering by customer
    
    Returns:
        tuple[dict, SyncOutcome]: Sync results envelope and service pull outcome
    """
    if customer_ref:
        logger.info(f"Syncing Invoices from QBO API for realm_id: {realm_id} (CustomerRef: {customer_ref})")
    elif start_date or end_date:
        logger.info(f"Syncing Invoices from QBO API for realm_id: {realm_id} (TxnDate: {start_date or 'beginning'} to {end_date or 'now'})")
    else:
        logger.info(f"Syncing Invoices from QBO API for realm_id: {realm_id}")
    
    # Fetch invoices from QBO and store locally (without auto-syncing to modules)
    outcome = qbo_invoice_service.sync_from_qbo(
        realm_id=realm_id,
        last_updated_time=last_sync_time,
        start_date=start_date,
        end_date=end_date,
        customer_ref=customer_ref,
        sync_to_modules=False,
    )
    invoices = outcome.synced
    
    if not invoices:
        logger.info(f"No Invoice updates found since {last_sync_time or 'beginning'}")
        return {
            "invoices_synced": 0,
            "invoices_module_synced": 0,
            "invoices": [],
        }, outcome
    
    logger.info(f"Retrieved {len(invoices)} invoices from QBO")

    # Pre-warm connector caches (bulk loads mapping tables once)
    invoice_connector.preload_caches()

    # Pre-load all QboInvoiceLines into a dict keyed by qbo_invoice_id
    logger.info("Pre-loading QboInvoiceLines for all invoices...")
    all_lines = qbo_invoice_service.line_repo.read_all()
    lines_by_invoice: dict = {}
    for line in all_lines:
        lines_by_invoice.setdefault(line.qbo_invoice_id, []).append(line)
    logger.info(f"Pre-loaded {len(all_lines)} QboInvoiceLines for {len(lines_by_invoice)} invoices")

    # Sync invoices to Invoice module sequentially

    for i, invoice in enumerate(invoices):
        try:
            invoice_lines = lines_by_invoice.get(invoice.id, [])
            invoice_module = with_retry(
                invoice_connector.sync_from_qbo_invoice,
                invoice,
                invoice_lines,
                max_retries=MAX_RETRIES,
                initial_delay=INITIAL_RETRY_DELAY,
            )
            outcome.record_projected()
            logger.info(f"Synced QboInvoice {invoice.id} to Invoice {invoice_module.id} ({i + 1}/{len(invoices)})")
        except Exception as e:
            outcome.record_projection_error(
                invoice.qbo_id, e, label="QboInvoice->Invoice", logger=logger
            )

        pace_batch(i, len(invoices), logger, "invoices")
    
    return {
        "invoices_synced": len(invoices),
        "invoices_module_synced": outcome.projected_count,
        "invoices": [invoice.to_dict() for invoice in invoices],
    }, outcome


def sync_qbo_invoice(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    customer_ref: Optional[str] = None,
    project: Optional[str] = None,
    skip_sync_record_update: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    One-way sync for QBO Invoices -> Invoice module.

    Fetches invoices from QBO, stores locally in qbo.Invoice/qbo.InvoiceLine,
    then syncs to Invoice/InvoiceLineItem modules via the connector.

    Args:
        start_date: Optional start date (YYYY-MM-DD) for filtering invoices by TxnDate.
        end_date: Optional end date (YYYY-MM-DD) for filtering invoices by TxnDate.
        customer_ref: Optional QBO Customer ID for filtering by customer.
        project: Optional project name to resolve to a QBO Customer ID.
        skip_sync_record_update: If True, don't update the sync record timestamp.
        dry_run: If True, fetch from QBO and report what would be synced without writing anything.
    """
    try:
        # Resolve --project to --customer-ref if provided
        if project and not customer_ref:
            customer_ref = _resolve_project_to_customer_ref(project)
        
        if customer_ref:
            logger.info(f"Customer filter: QBO CustomerRef = {customer_ref}")

        sync_service = SyncService()
        qbo_invoice_service = QboInvoiceService()
        invoice_connector = InvoiceInvoiceConnector()
        auth_service = QboAuthService()

        provider = 'qbo'
        entity = 'invoice'
        env = 'prod'

        run = WatermarkRun(sync_service, provider, env, entity).open()
        start_time_str = _normalize_watermark_value(run.query_start)
        logger.info(f"QBO Invoice sync triggered at: {start_time_str}")
        
        if start_date or end_date:
            logger.info(f"Date range filter: {start_date or 'beginning'} to {end_date or 'now'}")
        
        # Get realm ID
        realm_id = auth_service.resolve_realm_id()
        logger.info(f"Using realm_id: {realm_id}")

        # For date range or customer-specific queries, don't use last_sync_time
        # For regular incremental sync, use last_sync_time
        last_sync_time = None
        if start_date or end_date or customer_ref:
            logger.info("Filtered sync mode - using filters instead of last sync time")
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
                customer_ref=customer_ref,
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
            qbo_invoice_service=qbo_invoice_service,
            invoice_connector=invoice_connector,
            start_date=start_date,
            end_date=end_date,
            customer_ref=customer_ref,
        )

        end_time = datetime.now(timezone.utc)
        end_time_str = _normalize_last_sync(end_time.isoformat())

        skip_watermark = skip_sync_record_update or bool(customer_ref)
        if customer_ref:
            logger.info(
                "Skipping sync record update (customer-specific sync; watermark will not advance)"
            )
        updated_sync = run.commit(
            outcome,
            end_date=end_date,
            skip=skip_watermark,
        )
        
        result = {
            "success": True,
            "realm_id": realm_id,
            "start_time": start_time_str,
            "end_time": end_time_str,
            "customer_ref": customer_ref,
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
        
        logger.info(f"QBO Invoice sync completed. Invoices from QBO: {qbo_to_local_result['invoices_synced']}, "
                    f"Invoices module synced: {qbo_to_local_result['invoices_module_synced']}")
        
        return {
            "result": result,
            "status_code": 200,
        }

    except Exception as e:
        error_msg = f"Error syncing QBO Invoices: {str(e)}"
        logger.exception(error_msg)
        return {
            "result": {
                "success": False,
                "error": error_msg,
            },
            "status_code": 500,
        }


def run_locked(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    customer_ref: Optional[str] = None,
    project: Optional[str] = None,
    skip_sync_record_update: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Lock-wrapped entry point for a direct CLI run (`python scripts/sync_qbo_invoice.py`).

    This CLI invocation is a third path onto QboInvoiceService.sync_from_qbo,
    independent of the (already-locked) admin dispatcher — locking must live
    at this outer layer, not inside `sync_qbo_invoice()`, which the admin
    path also calls while already holding this same resource (see
    scripts/sync_qbo_account.py::run_locked for the full rationale).
    """
    lock_resource = qbo_entity_sync_lock_resource("invoice")
    with qbo_app_lock(lock_resource) as got_lock:
        if not got_lock:
            return {
                "result": {
                    "success": False,
                    "error": f"QBO invoice sync already in progress (lock '{lock_resource}' busy).",
                },
                "status_code": 409,
            }
        return sync_qbo_invoice(
            start_date=start_date,
            end_date=end_date,
            customer_ref=customer_ref,
            project=project,
            skip_sync_record_update=skip_sync_record_update,
            dry_run=dry_run,
        )


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Sync QBO Invoices to BuildOne (one-way: QBO -> local)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Examples:
  # Full incremental sync (uses last sync timestamp)
  python scripts/sync_qbo_invoice.py

  # Sync invoices for a specific project
  python scripts/sync_qbo_invoice.py --project WVA

  # Sync invoices by QBO Customer ID directly
  python scripts/sync_qbo_invoice.py --customer-ref 759

  # Sync invoices for a specific year
  python scripts/sync_qbo_invoice.py --start-date 2024-01-01 --end-date 2024-12-31

  # Combine filters
  python scripts/sync_qbo_invoice.py --project WVA --start-date 2024-01-01

  # Historical batch without updating sync record
  python scripts/sync_qbo_invoice.py --start-date 2022-01-01 --end-date 2022-12-31 --skip-sync-update

  # Dry run: see what would be synced without writing anything
  python scripts/sync_qbo_invoice.py --dry-run

{END_DATE_CLAMP_EPILOG_NOTE}
        """
    )

    parser.add_argument(
        '--project',
        type=str,
        help='Project name (or partial name) to sync invoices for. Resolves to QBO CustomerRef.',
        default=None
    )

    parser.add_argument(
        '--customer-ref',
        type=str,
        help='QBO Customer ID to filter invoices by. Alternative to --project.',
        default=None
    )

    parser.add_argument(
        '--start-date',
        type=str,
        help='Start date for filtering invoices by TxnDate (YYYY-MM-DD). Inclusive.',
        default=None
    )

    parser.add_argument(
        '--end-date',
        type=str,
        help='End date for filtering invoices by TxnDate (YYYY-MM-DD). Inclusive.',
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
        customer_ref=args.customer_ref,
        project=args.project,
        skip_sync_record_update=args.skip_sync_update,
        dry_run=args.dry_run,
    )
    
    import json
    print(json.dumps(result, indent=2, default=str))
    exit_nonzero_on_sync_failure(result)
