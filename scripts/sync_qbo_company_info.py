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
from integrations.intuit.qbo.auth.business.service import QboAuthService

logger = logging.getLogger(__name__)


def sync_qbo_company_info() -> dict:
    """
    """
    try:
        # Create start time variable
        start_time = datetime.now(timezone.utc)
        start_time_str = _normalize_last_sync(start_time.isoformat())
        logger.info(f"HTTP Function triggered at: {start_time_str}")
        
        # Initialize the services
        sync_service = SyncService()
        company_info_service = QboCompanyInfoService()
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
        
        # Sync CompanyInfo
        logger.info(f"Syncing CompanyInfo for realm_id: {realm_id}")
        company_info = company_info_service.sync_from_qbo(realm_id=realm_id)
        print(company_info)
        '''
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
            "sync_record": updated_sync.to_dict(),
            "start_time": start_time_str,
            "end_time": end_time_str,
            "realm_id": realm_id,
        }
        
        return {
            "result": result,
            "status_code": 200,
        }
        '''
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
