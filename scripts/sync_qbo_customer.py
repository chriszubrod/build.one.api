# Python Standard Library Imports
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
from scripts.sync_helper import _normalize_last_sync
from shared.database import with_retry, is_transient_error
from integrations.sync.business.service import SyncService
from integrations.sync.business.model import Sync
from integrations.intuit.qbo.customer.business.service import QboCustomerService
from integrations.intuit.qbo.customer.business.model import QboCustomer
from integrations.intuit.qbo.customer.connector.customer.business.service import CustomerCustomerConnector
from integrations.intuit.qbo.customer.connector.project.business.service import CustomerProjectConnector
from integrations.intuit.qbo.customer.connector.customer.persistence.repo import CustomerCustomerRepository
from integrations.intuit.qbo.customer.connector.project.persistence.repo import CustomerProjectRepository
from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository
from integrations.intuit.qbo.auth.business.service import QboAuthService

logger = logging.getLogger(__name__)

# Sync configuration
BATCH_SIZE = 10  # Process customers in batches
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


def _get_or_create_sync_record(sync_service: SyncService, provider: str, env: str, entity: str) -> Sync:
    """
    Get or create a Sync record for the given provider/env/entity.
    """
    all_syncs = sync_service.read_all()
    sync_record = next(
        (sync for sync in all_syncs if sync.provider == provider and sync.env == env and sync.entity == entity),
        None,
    )
    
    if not sync_record:
        sync_record = sync_service.create(
            provider=provider,
            env=env,
            entity=entity,
            last_sync_datetime=None,
        )
        logger.info(f"Created new sync record for {provider}/{env}/{entity}")
    
    return sync_record


def _update_sync_record(sync_service: SyncService, sync_record: Sync, end_time_str: str) -> Sync:
    """
    Update the sync record with new last_sync_datetime.
    """
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
    return updated_sync


def sync_qbo_to_local(
    realm_id: str,
    last_sync_time: Optional[str],
    qbo_customer_service: QboCustomerService,
    customer_connector: CustomerCustomerConnector,
    project_connector: CustomerProjectConnector,
) -> dict:
    """
    Sync Customers from QBO API to local database and modules.
    
    Args:
        realm_id: QBO realm ID
        last_sync_time: Last sync timestamp for incremental sync
        qbo_customer_service: QboCustomerService instance
        customer_connector: CustomerCustomerConnector instance
        project_connector: CustomerProjectConnector instance
    
    Returns:
        dict: Sync results including customers synced
    """
    logger.info(f"Syncing Customers from QBO API for realm_id: {realm_id}")
    
    # Fetch customers from QBO and store locally (without auto-syncing to modules)
    customers = qbo_customer_service.sync_from_qbo(
        realm_id=realm_id,
        last_updated_time=last_sync_time,
        sync_to_modules=False  # We'll handle module sync separately for better control
    )
    
    if not customers:
        logger.info(f"No Customer updates found since {last_sync_time or 'beginning'}")
        return {
            "customers_synced": 0,
            "customers_module_synced": 0,
            "projects_synced": 0,
            "customers": [],
        }
    
    logger.info(f"Retrieved {len(customers)} customers from QBO")
    
    # Separate parent customers and job customers
    parent_customers = [customer for customer in customers if customer.is_parent_customer]
    job_customers = [customer for customer in customers if customer.is_job]
    
    # Sync parent customers to Customer module first
    customers_module_synced = 0
    failed_parents = []
    
    for i, customer in enumerate(parent_customers):
        try:
            # Use retry logic for transient errors
            customer_module = with_retry(
                customer_connector.sync_from_qbo_customer,
                customer,
                max_retries=MAX_RETRIES,
                initial_delay=INITIAL_RETRY_DELAY,
            )
            customers_module_synced += 1
            logger.info(f"Synced QboCustomer {customer.id} to Customer {customer_module.id}")
        except Exception as e:
            logger.error(f"Failed to sync parent QboCustomer {customer.id} to Customer: {e}")
            failed_parents.append(customer.id)
        
        # Add delay between batches to keep connection alive
        if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(parent_customers):
            logger.debug(f"Processed {i + 1}/{len(parent_customers)} parent customers, pausing...")
            time.sleep(BATCH_DELAY)
    
    # Sync job customers to Project module
    projects_synced = 0
    failed_jobs = []
    
    for i, customer in enumerate(job_customers):
        try:
            # Use retry logic for transient errors
            project = with_retry(
                project_connector.sync_from_qbo_customer,
                customer,
                max_retries=MAX_RETRIES,
                initial_delay=INITIAL_RETRY_DELAY,
            )
            projects_synced += 1
            logger.info(f"Synced QboCustomer {customer.id} to Project {project.id}")
        except Exception as e:
            logger.error(f"Failed to sync job QboCustomer {customer.id} to Project: {e}")
            failed_jobs.append(customer.id)
        
        # Add delay between batches to keep connection alive
        if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(job_customers):
            logger.debug(f"Processed {i + 1}/{len(job_customers)} job customers, pausing...")
            time.sleep(BATCH_DELAY)
    
    if failed_parents:
        logger.warning(f"Failed to sync {len(failed_parents)} parent customers: {failed_parents}")
    if failed_jobs:
        logger.warning(f"Failed to sync {len(failed_jobs)} job customers: {failed_jobs}")
    
    return {
        "customers_synced": len(customers),
        "customers_module_synced": customers_module_synced,
        "projects_synced": projects_synced,
        "customers": [customer.to_dict() for customer in customers],
    }


def sync_local_to_qbo(
    realm_id: str,
    last_sync_time: Optional[str],
    qbo_customer_service: QboCustomerService,
    customer_mapping_repo: CustomerCustomerRepository,
    project_mapping_repo: CustomerProjectRepository,
    qbo_customer_repo: QboCustomerRepository,
) -> dict:
    """
    Sync locally modified Customers/Projects back to QBO.
    
    This is the reverse sync: local changes -> QBO Customers.
    
    Note: Currently, Customer and Project modules are not implemented,
    so this function is a placeholder for future implementation.
    
    Args:
        realm_id: QBO realm ID
        last_sync_time: Last sync timestamp to detect local modifications
        Various service/repo instances
    
    Returns:
        dict: Sync results
    """
    logger.info("Checking for local Customer/Project modifications to sync to QBO")
    
    customers_pushed = 0
    projects_pushed = 0
    
    # TODO: Implement reverse sync when Customer and Project modules are available
    # This would involve:
    # 1. Reading all Customers/Projects modified since last_sync_time
    # 2. Finding their QboCustomer mappings
    # 3. Comparing modification times
    # 4. Updating QboCustomer records if local is newer
    # 5. Optionally pushing to QBO API
    
    logger.info("Reverse sync not yet implemented - Customer/Project modules not available")
    
    return {
        "customers_pushed": customers_pushed,
        "projects_pushed": projects_pushed,
    }


def sync_qbo_customer() -> dict:
    """
    Two-way sync for QBO Customers <-> Customer/Project modules.
    
    1. QBO -> Local: Fetch customers modified since last sync, store locally, sync to Customer/Project
    2. Local -> QBO: Check for locally modified Customers/Projects, sync back to QboCustomer
    """
    try:
        # Create start time variable
        start_time = datetime.now(timezone.utc)
        start_time_str = _normalize_last_sync(start_time.isoformat())
        logger.info(f"QBO Customer sync triggered at: {start_time_str}")
        
        # Initialize services
        sync_service = SyncService()
        qbo_customer_service = QboCustomerService()
        qbo_customer_repo = QboCustomerRepository()
        customer_connector = CustomerCustomerConnector()
        project_connector = CustomerProjectConnector()
        customer_mapping_repo = CustomerCustomerRepository()
        project_mapping_repo = CustomerProjectRepository()
        auth_service = QboAuthService()
        
        # Get realm ID
        all_auths = auth_service.read_all()
        if not all_auths or len(all_auths) == 0:
            raise ValueError("No QBO authentication found. Please connect your QuickBooks account first.")
        realm_id = all_auths[0].realm_id
        logger.info(f"Using realm_id: {realm_id}")
        
        # Get or create Sync record
        provider = 'qbo'
        entity = 'customer'
        env = 'prod'
        
        sync_record = _get_or_create_sync_record(sync_service, provider, env, entity)
        
        # Get last sync time for incremental sync
        last_sync_time = None
        if sync_record and sync_record.last_sync_datetime:
            last_sync_time = sync_record.last_sync_datetime
            logger.info(f"Last sync time: {last_sync_time}. Fetching only updated records.")
        else:
            logger.info("No previous sync found. Performing full sync.")
        
        # Step 1: Sync from QBO to local
        qbo_to_local_result = sync_qbo_to_local(
            realm_id=realm_id,
            last_sync_time=last_sync_time,
            qbo_customer_service=qbo_customer_service,
            customer_connector=customer_connector,
            project_connector=project_connector,
        )
        
        # Step 2: Sync from local to QBO (reverse sync)
        local_to_qbo_result = sync_local_to_qbo(
            realm_id=realm_id,
            last_sync_time=last_sync_time,
            qbo_customer_service=qbo_customer_service,
            customer_mapping_repo=customer_mapping_repo,
            project_mapping_repo=project_mapping_repo,
            qbo_customer_repo=qbo_customer_repo,
        )
        
        # Update Sync record
        end_time = datetime.now(timezone.utc)
        end_time_str = _normalize_last_sync(end_time.isoformat())
        updated_sync = _update_sync_record(sync_service, sync_record, end_time_str)
        
        result = {
            "success": True,
            "realm_id": realm_id,
            "start_time": start_time_str,
            "end_time": end_time_str,
            "sync_record": updated_sync.to_dict(),
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


if __name__ == "__main__":
    result = sync_qbo_customer()
    print(result)
