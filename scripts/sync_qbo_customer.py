# Python Standard Library Imports
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
    assert_cli_system_admin,
    exit_nonzero_on_sync_failure,
)
from integrations.intuit.qbo.base.locking import qbo_sync_locked_cli
from integrations.intuit.qbo.base.watermark import (
    WatermarkRun,
    _normalize_last_sync,
    _normalize_watermark_value,
)
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from integrations.sync.business.service import SyncService
from integrations.intuit.qbo.customer.business.service import QboCustomerService
from integrations.intuit.qbo.customer.business.model import QboCustomer
from integrations.intuit.qbo.auth.business.service import QboAuthService

logger = logging.getLogger(__name__)


def sync_qbo_to_local(
    realm_id: str,
    last_sync_time: Optional[str],
    qbo_customer_service: QboCustomerService,
) -> tuple[dict, SyncOutcome]:
    """
    Sync Customers from QBO API to local database and modules.

    Args:
        realm_id: QBO realm ID
        last_sync_time: Last sync timestamp for incremental sync
        qbo_customer_service: QboCustomerService instance

    Returns:
        tuple[dict, SyncOutcome]: Sync results envelope and service pull outcome

    U-513 ph2 — the projection loops that used to live HERE are gone; the
    service owns projection now (`sync_to_modules=True`). This is the path the
    scheduler and `shared/api/admin.py` actually run, and while it projected
    for itself, the parent connector could not be handed the ORIGINAL QBO
    payload: the external records only ever exist inside
    `QboCustomerService.sync_from_qbo`'s staging loop, which is where the
    payload map is built. Projecting from here meant projecting from the
    `qbo.PhysicalAddress` staging rows instead — the table U-513 is sunsetting.

    The loops were not deletable until the service carried what they alone had:
    `with_retry` (transient-error retry) and `pace_batch` (inter-batch delay
    that keeps the DB connection alive under per-row load). Both now sit in the
    projection closures in `_sync_to_customers` / `_sync_to_projects`.

    Ordering is load-bearing and is now the service's to hold: parents project
    BEFORE jobs, because a job's billing fallback reads the `dbo.Address` its
    parent's projection mints.
    """
    logger.info(f"Syncing Customers from QBO API for realm_id: {realm_id}")

    # Fetch customers from QBO, store locally, AND project to Customer/Project.
    outcome = qbo_customer_service.sync_from_qbo(
        realm_id=realm_id,
        last_updated_time=last_sync_time,
        sync_to_modules=True,
    )
    customers = outcome.synced

    if not customers:
        logger.info(f"No Customer updates found since {last_sync_time or 'beginning'}")
        return {
            "customers_synced": 0,
            "customers_module_synced": 0,
            "projects_synced": 0,
            "customers": [],
        }, outcome

    logger.info(f"Retrieved {len(customers)} customers from QBO")

    # Per-module projection counts are DERIVED from the outcome rather than
    # tallied locally: every staged row was handed to a projection, so a row
    # projected unless its qbo_id came back as a projection failure or a skip.
    # `outcome.projected_count` is the two tiers COMBINED and cannot answer
    # "how many parents" vs "how many jobs", which the returned dict has always
    # reported separately.
    unprojected = set(outcome.projection_failed_ids) | set(outcome.skipped_ids)
    customers_module_synced = sum(
        1 for customer in customers
        if customer.is_parent_customer and str(customer.qbo_id) not in unprojected
    )
    projects_synced = sum(
        1 for customer in customers
        if customer.is_job and str(customer.qbo_id) not in unprojected
    )

    return {
        "customers_synced": len(customers),
        "customers_module_synced": customers_module_synced,
        "projects_synced": projects_synced,
        "customers": [customer.to_dict() for customer in customers],
    }, outcome


def sync_qbo_customer() -> dict:
    """
    One-way sync for QBO Customers -> Customer/Project modules (QBO -> Local only).

    1. QBO -> Local: Fetch customers modified since last sync, store locally, sync to Customer/Project

    Note: Local -> QBO push is disabled (one-way intake only). The prior
    reverse-sync helper (`sync_local_to_qbo`) was confirmed dead code (no
    caller ever invoked it -- this entrypoint always hardcoded its result to
    zero) and was deleted (U-314, mirrors U-307c's identical Item-sync cleanup).
    """
    try:
        sync_service = SyncService()
        qbo_customer_service = QboCustomerService()
        auth_service = QboAuthService()
        
        # Get realm ID
        realm_id = auth_service.resolve_realm_id()
        logger.info(f"Using realm_id: {realm_id}")
        
        provider = 'qbo'
        entity = 'customer'
        env = 'prod'

        run = WatermarkRun(sync_service, provider, env, entity).open()
        start_time_str = _normalize_watermark_value(run.query_start)
        logger.info(f"QBO Customer sync triggered at: {start_time_str}")

        last_sync_time = None
        if run.last_sync_time:
            last_sync_time = run.last_sync_time
            logger.info(f"Last sync time: {last_sync_time}. Fetching only updated records.")
        else:
            logger.info("No previous sync found. Performing full sync.")

        qbo_to_local_result, outcome = sync_qbo_to_local(
            realm_id=realm_id,
            last_sync_time=last_sync_time,
            qbo_customer_service=qbo_customer_service,
        )
        
        # Step 2: Local -> QBO push disabled (one-way intake only).
        local_to_qbo_result = {"customers_pushed": 0, "projects_pushed": 0}
        
        end_time = datetime.now(timezone.utc)
        end_time_str = _normalize_last_sync(end_time.isoformat())
        updated_sync = run.commit(outcome)

        result = {
            "success": True,
            "realm_id": realm_id,
            "start_time": start_time_str,
            "end_time": end_time_str,
            "sync_record": updated_sync.to_dict(),
            "watermark": {
                **outcome.summary(),
                "committed_last_sync_datetime": updated_sync.last_sync_datetime,
            },
            "qbo_to_local": qbo_to_local_result,
            "local_to_qbo": local_to_qbo_result,
        }
        
        logger.info(f"QBO Customer sync completed. Customers from QBO: {qbo_to_local_result['customers_synced']}, "
                    f"Customers module synced: {qbo_to_local_result['customers_module_synced']}, "
                    f"Projects synced: {qbo_to_local_result['projects_synced']}, "
                    f"Customers pushed: {local_to_qbo_result['customers_pushed']}, "
                    f"Projects pushed: {local_to_qbo_result['projects_pushed']}")
        
        return {
            "result": result,
            "status_code": 200,
        }

    except Exception as e:
        error_msg = f"Error syncing QBO Customers: {str(e)}"
        logger.exception(error_msg)
        return {
            "result": {
                "success": False,
                "error": error_msg,
            },
            "status_code": 500,
        }


@qbo_sync_locked_cli("customer")
def run_locked() -> dict:
    """
    Lock-wrapped entry point for a direct CLI run (`python scripts/sync_qbo_customer.py`).

    This CLI invocation is a third path onto QboCustomerService.sync_from_qbo,
    independent of the (already-locked) admin dispatcher — locking must live
    at this outer layer, not inside `sync_qbo_customer()`, which the admin
    path also calls while already holding this same resource (see
    scripts/sync_qbo_account.py::run_locked for the full rationale).
    """
    return sync_qbo_customer()


if __name__ == "__main__":
    assert_cli_system_admin()
    result = run_locked()
    print(result)
    exit_nonzero_on_sync_failure(result)
