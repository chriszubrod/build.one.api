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
from scripts.sync_helper import (
    WatermarkRun,
    _normalize_last_sync,
    _normalize_watermark_value,
    assert_cli_system_admin,
    exit_nonzero_on_sync_failure,
)
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from shared.database import with_retry
from integrations.sync.business.service import SyncService
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


def sync_qbo_to_local(
    realm_id: str,
    last_sync_time: Optional[str],
    qbo_customer_service: QboCustomerService,
    customer_connector: CustomerCustomerConnector,
    project_connector: CustomerProjectConnector,
) -> tuple[dict, SyncOutcome]:
    """
    Sync Customers from QBO API to local database and modules.
    
    Args:
        realm_id: QBO realm ID
        last_sync_time: Last sync timestamp for incremental sync
        qbo_customer_service: QboCustomerService instance
        customer_connector: CustomerCustomerConnector instance
        project_connector: CustomerProjectConnector instance
    
    Returns:
        tuple[dict, SyncOutcome]: Sync results envelope and service pull outcome
    """
    logger.info(f"Syncing Customers from QBO API for realm_id: {realm_id}")
    
    # Fetch customers from QBO and store locally (without auto-syncing to modules)
    outcome = qbo_customer_service.sync_from_qbo(
        realm_id=realm_id,
        last_updated_time=last_sync_time,
        sync_to_modules=False  # We'll handle module sync separately for better control
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
    
    # Separate parent customers and job customers
    parent_customers = [customer for customer in customers if customer.is_parent_customer]
    job_customers = [customer for customer in customers if customer.is_job]
    
    # Sync parent customers to Customer module first
    customers_module_synced = 0
    
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
            outcome.record_projected()
            logger.info(f"Synced QboCustomer {customer.id} to Customer {customer_module.id}")
        except Exception as e:
            outcome.record_projection_error(
                customer.id, e, label="QboCustomer->Customer", logger=logger
            )
        
        # Add delay between batches to keep connection alive
        if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(parent_customers):
            logger.debug(f"Processed {i + 1}/{len(parent_customers)} parent customers, pausing...")
            time.sleep(BATCH_DELAY)
    
    # Sync job customers to Project module
    projects_synced = 0
    
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
            outcome.record_projected()
            logger.info(f"Synced QboCustomer {customer.id} to Project {project.id}")
        except Exception as e:
            outcome.record_projection_error(
                customer.id, e, label="QboCustomer->Project", logger=logger
            )
        
        # Add delay between batches to keep connection alive
        if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(job_customers):
            logger.debug(f"Processed {i + 1}/{len(job_customers)} job customers, pausing...")
            time.sleep(BATCH_DELAY)
    
    return {
        "customers_synced": len(customers),
        "customers_module_synced": customers_module_synced,
        "projects_synced": projects_synced,
        "customers": [customer.to_dict() for customer in customers],
    }, outcome


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
    One-way sync for QBO Customers -> Customer/Project modules (QBO -> Local only).

    1. QBO -> Local: Fetch customers modified since last sync, store locally, sync to Customer/Project

    Note: Local -> QBO push is disabled in the batch sync process.
    The sync_local_to_qbo function is preserved for one-time pushes
    when a record is marked Complete.
    """
    try:
        sync_service = SyncService()
        qbo_customer_service = QboCustomerService()
        qbo_customer_repo = QboCustomerRepository()
        customer_connector = CustomerCustomerConnector()
        project_connector = CustomerProjectConnector()
        customer_mapping_repo = CustomerCustomerRepository()
        project_mapping_repo = CustomerProjectRepository()
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
            customer_connector=customer_connector,
            project_connector=project_connector,
        )
        
        # Step 2: Local -> QBO push disabled in batch sync (one-way intake only).
        # The sync_local_to_qbo function is preserved for one-time pushes
        # when a record is marked Complete.
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


if __name__ == "__main__":
    assert_cli_system_admin()
    result = sync_qbo_customer()
    print(result)
    exit_nonzero_on_sync_failure(result)
