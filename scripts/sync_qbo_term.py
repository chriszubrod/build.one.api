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
)
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from shared.database import with_retry
from integrations.sync.business.service import SyncService
from integrations.intuit.qbo.term.business.service import QboTermService
from integrations.intuit.qbo.term.business.model import QboTerm
from integrations.intuit.qbo.term.connector.payment_term.business.service import TermPaymentTermConnector
from integrations.intuit.qbo.term.connector.payment_term.persistence.repo import TermPaymentTermRepository
from integrations.intuit.qbo.term.persistence.repo import QboTermRepository
from integrations.intuit.qbo.auth.business.service import QboAuthService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Sync configuration
BATCH_SIZE = 10  # Process terms in batches
BATCH_DELAY = 0.5  # Delay between batches (seconds)
MAX_RETRIES = 3  # Max retries for transient errors
INITIAL_RETRY_DELAY = 2.0  # Initial retry delay (seconds)


def sync_qbo_to_local(
    realm_id: str,
    last_sync_time: Optional[str],
    qbo_term_service: QboTermService,
    term_connector: TermPaymentTermConnector,
    outcome: SyncOutcome,
) -> dict:
    """
    Sync Terms from QBO API to local database and PaymentTerm module.
    
    Args:
        realm_id: QBO realm ID
        last_sync_time: Last sync timestamp for incremental sync
        qbo_term_service: QboTermService instance
        term_connector: TermPaymentTermConnector instance
    
    Returns:
        dict: Sync results including terms synced
    """
    logger.info(f"Syncing Terms from QBO API for realm_id: {realm_id}")
    
    # Fetch terms from QBO and store locally (without auto-syncing to modules)
    terms = qbo_term_service.sync_from_qbo(
        realm_id=realm_id,
        last_updated_time=last_sync_time,
        sync_to_modules=False,  # We'll handle module sync separately for better control
        outcome=outcome,
    )
    
    if not terms:
        logger.info(f"No Term updates found since {last_sync_time or 'beginning'}")
        return {
            "terms_synced": 0,
            "payment_terms_synced": 0,
            "terms": [],
        }
    
    logger.info(f"Retrieved {len(terms)} terms from QBO")
    
    # Sync terms to PaymentTerm module
    payment_terms_synced = 0
    
    for i, term in enumerate(terms):
        try:
            # Use retry logic for transient errors
            payment_term = with_retry(
                term_connector.sync_from_qbo_term,
                term,
                max_retries=MAX_RETRIES,
                initial_delay=INITIAL_RETRY_DELAY,
            )
            payment_terms_synced += 1
            logger.info(f"Synced QboTerm {term.id} to PaymentTerm {payment_term.id}")
        except Exception as e:
            outcome.record_projection_error(
                term.id, e, label="QboTerm->PaymentTerm", logger=logger
            )
        
        # Add delay between batches to keep connection alive
        if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(terms):
            logger.debug(f"Processed {i + 1}/{len(terms)} terms, pausing...")
            time.sleep(BATCH_DELAY)
    
    return {
        "terms_synced": len(terms),
        "payment_terms_synced": payment_terms_synced,
        "terms": [term.to_dict() for term in terms],
    }


def sync_existing_terms_to_payment_terms(
    qbo_term_repo: QboTermRepository,
    term_connector: TermPaymentTermConnector,
    term_mapping_repo: TermPaymentTermRepository,
    outcome: SyncOutcome,
) -> dict:
    """
    Sync all existing QboTerm records to PaymentTerm module.
    
    This is useful when QboTerm records were synced before the connector
    was set up, or to re-sync all records.
    
    Args:
        qbo_term_repo: QboTermRepository instance
        term_connector: TermPaymentTermConnector instance
        term_mapping_repo: TermPaymentTermRepository instance
    
    Returns:
        dict: Sync results
    """
    logger.info("Syncing existing QboTerm records to PaymentTerm module")
    
    # Read all existing QboTerm records
    all_terms = qbo_term_repo.read_all()
    
    if not all_terms:
        logger.info("No existing QboTerm records found")
        return {
            "terms_processed": 0,
            "payment_terms_synced": 0,
            "skipped": 0,
        }
    
    logger.info(f"Found {len(all_terms)} existing QboTerm records")
    
    payment_terms_synced = 0
    skipped = 0
    
    for i, term in enumerate(all_terms):
        try:
            # Check if mapping already exists
            existing_mapping = term_mapping_repo.read_by_qbo_term_id(term.id)
            if existing_mapping:
                logger.debug(f"QboTerm {term.id} already mapped to PaymentTerm {existing_mapping.payment_term_id}, skipping")
                skipped += 1
                continue
            
            # Use retry logic for transient errors
            payment_term = with_retry(
                term_connector.sync_from_qbo_term,
                term,
                max_retries=MAX_RETRIES,
                initial_delay=INITIAL_RETRY_DELAY,
            )
            payment_terms_synced += 1
            logger.info(f"Synced QboTerm {term.id} ({term.name}) to PaymentTerm {payment_term.id}")
        except Exception as e:
            outcome.record_projection_error(
                term.id, e, label="QboTerm->PaymentTerm", logger=logger
            )
        
        # Add delay between batches to keep connection alive
        if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(all_terms):
            logger.debug(f"Processed {i + 1}/{len(all_terms)} terms, pausing...")
            time.sleep(BATCH_DELAY)
    
    return {
        "terms_processed": len(all_terms),
        "payment_terms_synced": payment_terms_synced,
        "skipped": skipped,
    }


def sync_local_to_qbo(
    realm_id: str,
    last_sync_time: Optional[str],
    qbo_term_service: QboTermService,
    term_mapping_repo: TermPaymentTermRepository,
    qbo_term_repo: QboTermRepository,
) -> dict:
    """
    Sync locally modified PaymentTerms back to QBO.
    
    This is the reverse sync: local changes -> QBO Terms.
    
    Note: Currently, PaymentTerm module modifications are not tracked,
    so this function is a placeholder for future implementation.
    
    Args:
        realm_id: QBO realm ID
        last_sync_time: Last sync timestamp to detect local modifications
        Various service/repo instances
    
    Returns:
        dict: Sync results
    """
    logger.info("Checking for local PaymentTerm modifications to sync to QBO")
    
    terms_pushed = 0
    
    # TODO: Implement reverse sync when PaymentTerm module modification tracking is available
    # This would involve:
    # 1. Reading all PaymentTerms modified since last_sync_time
    # 2. Finding their QboTerm mappings
    # 3. Comparing modification times
    # 4. Updating QboTerm records if local is newer
    # 5. Optionally pushing to QBO API
    
    logger.info("Reverse sync not yet implemented - PaymentTerm module modification tracking not available")
    
    return {
        "terms_pushed": terms_pushed,
    }


def sync_qbo_term(resync_existing: bool = False) -> dict:
    """
    One-way sync for QBO Terms -> PaymentTerm module (QBO -> Local only).

    1. QBO -> Local: Fetch terms modified since last sync, store locally, sync to PaymentTerm
    2. Existing -> Module: Sync any existing QboTerm records that aren't mapped yet

    Note: Local -> QBO push is disabled in the batch sync process.
    The sync_local_to_qbo function is preserved for one-time pushes
    when a record is marked Complete.
    
    Args:
        resync_existing: If True, sync all existing QboTerm records to PaymentTerm
    """
    try:
        sync_service = SyncService()
        qbo_term_service = QboTermService()
        qbo_term_repo = QboTermRepository()
        term_connector = TermPaymentTermConnector()
        term_mapping_repo = TermPaymentTermRepository()
        auth_service = QboAuthService()
        
        # Get realm ID
        all_auths = auth_service.read_all()
        if not all_auths or len(all_auths) == 0:
            raise ValueError("No QBO authentication found. Please connect your QuickBooks account first.")
        realm_id = all_auths[0].realm_id
        logger.info(f"Using realm_id: {realm_id}")
        
        provider = 'qbo'
        entity = 'term'
        env = 'prod'

        run = WatermarkRun(sync_service, provider, env, entity).open()
        start_time_str = _normalize_watermark_value(run.query_start)
        logger.info(f"QBO Term sync triggered at: {start_time_str}")

        last_sync_time = None
        if run.last_sync_time:
            last_sync_time = run.last_sync_time
            logger.info(f"Last sync time: {last_sync_time}. Fetching only updated records.")
        else:
            logger.info("No previous sync found. Performing full sync.")

        outcome = SyncOutcome()
        qbo_to_local_result = sync_qbo_to_local(
            realm_id=realm_id,
            last_sync_time=last_sync_time,
            qbo_term_service=qbo_term_service,
            term_connector=term_connector,
            outcome=outcome,
        )

        existing_sync_result = sync_existing_terms_to_payment_terms(
            qbo_term_repo=qbo_term_repo,
            term_connector=term_connector,
            term_mapping_repo=term_mapping_repo,
            outcome=outcome,
        )
        
        # Step 3: Local -> QBO push disabled in batch sync (one-way intake only).
        # The sync_local_to_qbo function is preserved for one-time pushes
        # when a record is marked Complete.
        local_to_qbo_result = {"terms_pushed": 0}
        
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
            "existing_to_module": existing_sync_result,
            "local_to_qbo": local_to_qbo_result,
        }
        
        logger.info(f"QBO Term sync completed. "
                    f"Terms from QBO: {qbo_to_local_result['terms_synced']}, "
                    f"PaymentTerms synced (from QBO): {qbo_to_local_result['payment_terms_synced']}, "
                    f"Existing synced: {existing_sync_result['payment_terms_synced']}, "
                    f"Skipped (already mapped): {existing_sync_result['skipped']}, "
                    f"Terms pushed: {local_to_qbo_result['terms_pushed']}")
        
        return {
            "result": result,
            "status_code": 200,
        }

    except Exception as e:
        error_msg = f"Error syncing QBO Terms: {str(e)}"
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
    result = sync_qbo_term()
    print(result)
