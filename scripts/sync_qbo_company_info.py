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
    WatermarkRun,
    _normalize_last_sync,
    _normalize_watermark_value,
    assert_cli_system_admin,
)
from integrations.sync.business.service import SyncService
from integrations.intuit.qbo.company_info.business.service import QboCompanyInfoService
from integrations.intuit.qbo.company_info.connector.business.service import CompanyInfoCompanyConnector
from integrations.intuit.qbo.physical_address.connector.business.service import PhysicalAddressAddressConnector
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
        address_connector = PhysicalAddressAddressConnector()
        auth_service = QboAuthService()

        provider = 'qbo'
        entity = 'company_info'
        env = 'prod'

        run = WatermarkRun(sync_service, provider, env, entity).open()
        start_time_str = _normalize_watermark_value(run.query_start)
        logger.info(f"HTTP Function triggered at: {start_time_str}")

        all_auths = auth_service.read_all()
        if not all_auths or len(all_auths) == 0:
            raise ValueError("No QBO authentication found. Please connect your QuickBooks account first.")
        realm_id = all_auths[0].realm_id

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

        # Sync PhysicalAddress records to Address module via connector
        addresses_synced = []
        address_ids_to_sync = [
            company_info.company_addr_id,
            company_info.legal_addr_id,
            company_info.customer_communication_addr_id,
        ]
        for addr_id in address_ids_to_sync:
            if addr_id:
                try:
                    address = address_connector.sync_from_qbo_to_address(qbo_physical_address_id=addr_id)
                    addresses_synced.append(address.id if address else None)
                    logger.info(f"Successfully synced PhysicalAddress {addr_id} to Address module. Address ID: {address.id if address else 'None'}")
                except Exception as e:
                    outcome.record_projection_error(
                        addr_id, e, label="PhysicalAddress->Address", logger=logger
                    )
                    addresses_synced.append(None)

        # Sync CompanyInfo to Company module via connector
        company = None
        if company_info and company_info.id:
            logger.info(f"Syncing CompanyInfo to Company module for QboCompanyInfo ID: {company_info.id}")
            try:
                company = company_connector.sync_from_qbo_to_company(
                    qbo_company_info_id=company_info.id,
                    realm_id=realm_id
                )
                outcome.record_projected()
                logger.info(f"Successfully synced to Company module. Company ID: {company.id}")
            except Exception as e:
                outcome.record_projection_error(
                    company_info.id, e, label="QboCompanyInfo->Company", logger=logger
                )
        else:
            logger.warning("CompanyInfo sync completed but no ID found. Skipping Company module sync.")

        end_time = datetime.now(timezone.utc)
        end_time_str = _normalize_last_sync(end_time.isoformat())
        updated_sync = run.commit(outcome)

        result = {
            "success": True,
            "company_info": company_info.to_dict(),
            "company": company.to_dict() if company else None,
            "addresses_synced": addresses_synced,
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


if __name__ == "__main__":
    assert_cli_system_admin()
    result = sync_qbo_company_info()
    print(result)
