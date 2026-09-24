# Python Standard Library Imports
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

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
from integrations.sync.business.service import SyncService
from integrations.intuit.qbo.company_info.business.service import QboCompanyInfoService
from integrations.intuit.qbo.company_info.connector.business.service import CompanyInfoCompanyConnector
from integrations.intuit.qbo.auth.business.service import QboAuthService

logger = logging.getLogger(__name__)


def sync_qbo_company_info() -> dict:
    """
    Sync CompanyInfo from QBO API and then sync to Company module via connector.
    """
    try:
        sync_service = SyncService()
        company_info_service = QboCompanyInfoService()
        company_connector = CompanyInfoCompanyConnector()
        auth_service = QboAuthService()

        provider = 'qbo'
        entity = 'company_info'
        env = 'prod'

        run = WatermarkRun(sync_service, provider, env, entity).open()
        start_time_str = _normalize_watermark_value(run.query_start)
        logger.info(f"HTTP Function triggered at: {start_time_str}")

        realm_id = auth_service.resolve_realm_id()

        last_sync_time = None
        if run.last_sync_time:
            last_sync_time = run.last_sync_time
            logger.info(f"Last sync time: {last_sync_time}. Fetching only updated records.")
        else:
            logger.info("No previous sync found. Fetching all CompanyInfo records.")

        # Sync CompanyInfo from QBO API
        logger.info(f"Syncing CompanyInfo from QBO API for realm_id: {realm_id}")
        outcome = company_info_service.sync_from_qbo(
            realm_id=realm_id,
            last_updated_time=last_sync_time
        )

        # QBO returns empty synced when nothing changed since the watermark — not a failure; still commit.
        if not outcome.synced:
            logger.info("No CompanyInfo updates found since last sync.")
            end_time = datetime.now(timezone.utc)
            end_time_str = _normalize_last_sync(end_time.isoformat())
            updated_sync = run.commit(outcome)

            result = {
                "success": True,
                "company_info": None,
                "company": None,
                "sync_record": updated_sync.to_dict(),
                "watermark": {
                    **outcome.summary(),
                    "committed_last_sync_datetime": updated_sync.last_sync_datetime,
                },
                "start_time": start_time_str,
                "end_time": end_time_str,
                "realm_id": realm_id,
                "message": "No updates found since last sync",
            }
            return {
                "result": result,
                "status_code": 200,
            }

        company_info = outcome.synced[0]

        # The three addresses are already projected. U-513 moved that into
        # `QboCompanyInfoService._project_addresses_from_payload`, which reads
        # them off the INLINE CompanyInfo payload; this loop used to take the
        # `qbo.PhysicalAddress` row ids the pull had just written and hand each
        # back to `PhysicalAddressAddressConnector.sync_from_qbo_to_address`,
        # which read the same row back out. Phase 1 removed that read (the
        # dependency on the table being sunset) and phase 3a removed the write
        # behind it, so this pull now touches no `qbo.*` staging table at all.
        # The service still records one `record_projection_error` per failing
        # slot onto this same outcome, so hold-vs-skip and the
        # `run.commit(outcome)` below are unchanged. The `addresses_synced`
        # response key went with the loop — it had no consumer, and address
        # failures already surface through `outcome.summary()`.
        #
        # NB `company_info.to_dict()` below still carries `company_addr_id` /
        # `legal_addr_id` / `customer_communication_addr_id`, now always None:
        # they held the staging row PKs. A None there does NOT mean the company
        # has no address.

        # Sync CompanyInfo to Company module via connector
        # No truthiness pre-guard here, deliberately: `_build_company_info` now
        # refuses a record with no QBO Id, so `outcome.synced` cannot contain
        # one. The guard this replaced had an `else` that skipped the projection
        # while recording NOTHING on the outcome -- the exact shape that let the
        # watermark advance past an unprojected Company. Every sibling script
        # (sync_qbo_item.py, sync_qbo_vendor.py, sync_qbo_term.py) calls its
        # connector straight inside try/except and lets record_projection_error
        # classify; this was the only truthiness pre-guard around a projection
        # call in any of the 11 sync scripts.
        company = None
        logger.info(f"Syncing CompanyInfo to Company module for QBO id: {company_info.qbo_id}")
        try:
            company = company_connector.sync_from_qbo_to_company(
                qbo_company_info=company_info,
                realm_id=realm_id
            )
            outcome.record_projected()
            logger.info(f"Successfully synced to Company module. Company ID: {company.id}")
        except Exception as e:
            outcome.record_projection_error(
                company_info.qbo_id, e, label="QboCompanyInfo->Company", logger=logger
            )

        end_time = datetime.now(timezone.utc)
        end_time_str = _normalize_last_sync(end_time.isoformat())
        updated_sync = run.commit(outcome)

        result = {
            "success": True,
            "company_info": company_info.to_dict(),
            "company": company.to_dict() if company else None,
            "sync_record": updated_sync.to_dict(),
            "watermark": {
                **outcome.summary(),
                "committed_last_sync_datetime": updated_sync.last_sync_datetime,
            },
            "start_time": start_time_str,
            "end_time": end_time_str,
            "realm_id": realm_id,
        }

        return {
            "result": result,
            "status_code": 200,
        }

    except Exception as e:
        error_msg = f"Error syncing CompanyInfo: {str(e)}"
        logger.exception(error_msg)
        return {
            "result": {
                "success": False,
                "error": error_msg,
            },
            "status_code": 500,
        }


@qbo_sync_locked_cli("company_info")
def run_locked() -> dict:
    """
    Lock-wrapped entry point for a direct CLI run (`python scripts/sync_qbo_company_info.py`).

    This CLI invocation is a third path onto QboCompanyInfoService.sync_from_qbo,
    independent of the (already-locked) admin dispatcher — locking must live
    at this outer layer, not inside `sync_qbo_company_info()`, which the admin
    path also calls while already holding this same resource (see
    scripts/sync_qbo_account.py::run_locked for the full rationale).
    """
    return sync_qbo_company_info()


if __name__ == "__main__":
    assert_cli_system_admin()
    result = run_locked()
    print(result)
    exit_nonzero_on_sync_failure(result)
