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
from integrations.intuit.qbo.vendor.business.service import QboVendorService
from integrations.intuit.qbo.vendor.business.model import QboVendor
from integrations.intuit.qbo.auth.business.service import QboAuthService

logger = logging.getLogger(__name__)


def sync_qbo_to_local(
    realm_id: str,
    last_sync_time: Optional[str],
    qbo_vendor_service: QboVendorService,
) -> tuple[dict, SyncOutcome]:
    """
    Sync Vendors from QBO API to local database and modules.

    U-513 ph2: staging AND projection both run inside
    `QboVendorService.sync_from_qbo(sync_to_modules=True)`. This script used to
    stage with `sync_to_modules=False` and then run its own projection loop —
    which meant the production path (this script, imported by
    `shared/scheduler.py` and `shared/api/admin.py`) never carried the inline
    QBO payload to the connector and silently kept reading addresses back out
    of `qbo.PhysicalAddress`. The service's projection closure now owns the
    `with_retry` / `pace_batch` this loop used to provide, so nothing is lost
    by deleting it; see `QboVendorService._sync_to_vendors`.

    Args:
        realm_id: QBO realm ID
        last_sync_time: Last sync timestamp for incremental sync
        qbo_vendor_service: QboVendorService instance

    Returns:
        tuple[dict, SyncOutcome]: Sync results envelope and service pull outcome
    """
    logger.info(f"Syncing Vendors from QBO API for realm_id: {realm_id}")

    # Fetch vendors from QBO, store locally, and project to the Vendor module
    outcome = qbo_vendor_service.sync_from_qbo(
        realm_id=realm_id,
        last_updated_time=last_sync_time,
        sync_to_modules=True,
    )
    vendors = outcome.synced

    if not vendors:
        logger.info(f"No Vendor updates found since {last_sync_time or 'beginning'}")
        return {
            "vendors_synced": 0,
            "vendors_module_synced": 0,
            "vendors": [],
        }, outcome

    logger.info(f"Retrieved {len(vendors)} vendors from QBO")

    return {
        "vendors_synced": len(vendors),
        # Projection successes are counted by the service's projection loop.
        "vendors_module_synced": outcome.projected_count,
        "vendors": [vendor.to_dict() for vendor in vendors],
    }, outcome


def sync_qbo_vendor() -> dict:
    """
    One-way sync for QBO Vendors -> Vendor module (QBO -> Local only).

    1. QBO -> Local: Fetch vendors modified since last sync, store locally, sync to Vendor

    Note: Local -> QBO push is disabled (one-way intake only). The prior
    reverse-sync helper (`sync_local_to_qbo`) was confirmed dead code (no
    caller ever invoked it -- this entrypoint always hardcoded its result to
    zero) and was deleted (U-314, mirrors U-307c's identical Item-sync cleanup).
    """
    try:
        sync_service = SyncService()
        qbo_vendor_service = QboVendorService()
        auth_service = QboAuthService()
        
        # Get realm ID
        realm_id = auth_service.resolve_realm_id()
        logger.info(f"Using realm_id: {realm_id}")
        
        provider = 'qbo'
        entity = 'vendor'
        env = 'prod'

        run = WatermarkRun(sync_service, provider, env, entity).open()
        start_time_str = _normalize_watermark_value(run.query_start)
        logger.info(f"QBO Vendor sync triggered at: {start_time_str}")

        last_sync_time = None
        if run.last_sync_time:
            last_sync_time = run.last_sync_time
            logger.info(f"Last sync time: {last_sync_time}. Fetching only updated records.")
        else:
            logger.info("No previous sync found. Performing full sync.")

        qbo_to_local_result, outcome = sync_qbo_to_local(
            realm_id=realm_id,
            last_sync_time=last_sync_time,
            qbo_vendor_service=qbo_vendor_service,
        )
        
        # Step 2: Local -> QBO push disabled (one-way intake only).
        local_to_qbo_result = {"vendors_pushed": 0}
        
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
        
        logger.info(f"QBO Vendor sync completed. Vendors from QBO: {qbo_to_local_result['vendors_synced']}, "
                    f"Vendors module synced: {qbo_to_local_result['vendors_module_synced']}, "
                    f"Vendors pushed: {local_to_qbo_result['vendors_pushed']}")
        
        return {
            "result": result,
            "status_code": 200,
        }

    except Exception as e:
        error_msg = f"Error syncing QBO Vendors: {str(e)}"
        logger.exception(error_msg)
        return {
            "result": {
                "success": False,
                "error": error_msg,
            },
            "status_code": 500,
        }


@qbo_sync_locked_cli("vendor")
def run_locked() -> dict:
    """
    Lock-wrapped entry point for a direct CLI run (`python scripts/sync_qbo_vendor.py`).

    This CLI invocation is a third path onto QboVendorService.sync_from_qbo,
    independent of the (already-locked) admin dispatcher — locking must live
    at this outer layer, not inside `sync_qbo_vendor()`, which the admin
    path also calls while already holding this same resource (see
    scripts/sync_qbo_account.py::run_locked for the full rationale).
    """
    return sync_qbo_vendor()


if __name__ == "__main__":
    assert_cli_system_admin()
    result = run_locked()
    print(result)
    exit_nonzero_on_sync_failure(result)
