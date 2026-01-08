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
from scripts.sync_helper import _normalize_last_sync
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
        # Create start time variable
        start_time = datetime.now(timezone.utc)
        start_time_str = _normalize_last_sync(start_time.isoformat())
        logger.info(f"HTTP Function triggered at: {start_time_str}")
        
        # Initialize the services
        sync_service = SyncService()
        company_info_service = QboCompanyInfoService()
        company_connector = CompanyInfoCompanyConnector()
        address_connector = PhysicalAddressAddressConnector()
        auth_service = QboAuthService()
        
        
        all_auths = auth_service.read_all()
        if not all_auths or len(all_auths) == 0:
            raise ValueError("No QBO authentication found. Please connect your QuickBooks account first.")
        realm_id = all_auths[0].realm_id
        #print(f"Realm ID: {realm_id}")
        
        
        # Get or create Sync record
        provider = 'qbo'
        entity = 'company_info'
        env = 'prod'
        
        all_syncs = sync_service.read_all()
        sync_record = next(
            (sync for sync in all_syncs if sync.provider == provider and sync.env == env and sync.entity == entity),
            None,
        )
        #print(f"Sync record: {sync_record}")

        if not sync_record:
            sync_record = sync_service.create(
                provider=provider,
                env=env,
                entity=entity,
                last_sync_datetime=None,
            )
            print(sync_record)
        
        # Get last sync time for incremental sync
        last_sync_time = None
        if sync_record and sync_record.last_sync_datetime:
            last_sync_time = sync_record.last_sync_datetime
            logger.info(f"Last sync time: {last_sync_time}. Fetching only updated records.")
        else:
            logger.info("No previous sync found. Fetching all CompanyInfo records.")
        
        # Sync CompanyInfo from QBO API
        logger.info(f"Syncing CompanyInfo from QBO API for realm_id: {realm_id}")
        company_info = company_info_service.sync_from_qbo(
            realm_id=realm_id,
            last_updated_time=last_sync_time
        )
        
        # If no updates found, still update sync record timestamp and return early
        if not company_info:
            logger.info("No CompanyInfo updates found. Updating sync timestamp.")
            end_time = datetime.now(timezone.utc)
            end_time_str = _normalize_last_sync(end_time.isoformat())
            
            from integrations.sync.business.model import Sync
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
            
            result = {
                "success": True,
                "company_info": None,
                "company": None,
                "sync_record": updated_sync.to_dict(),
                "start_time": start_time_str,
                "end_time": end_time_str,
                "realm_id": realm_id,
                "message": "No updates found since last sync",
            }
            return {
                "result": result,
                "status_code": 200,
            }
        
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
                    logger.warning(f"Failed to sync PhysicalAddress {addr_id} to Address module: {e}")
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
                logger.info(f"Successfully synced to Company module. Company ID: {company.id}")
            except Exception as e:
                logger.warning(f"Failed to sync CompanyInfo to Company module: {e}")
                # Continue execution even if connector sync fails
        else:
            logger.warning("CompanyInfo sync completed but no ID found. Skipping Company module sync.")

        # Update Sync record
        end_time = datetime.now(timezone.utc)
        end_time_str = _normalize_last_sync(end_time.isoformat())
        
        from integrations.sync.business.model import Sync
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
        
        result = {
            "success": True,
            "company_info": company_info.to_dict(),
            "company": company.to_dict() if company else None,
            "addresses_synced": addresses_synced,
            "sync_record": updated_sync.to_dict(),
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
    result = sync_qbo_company_info()
    print(result)
