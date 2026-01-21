# Python Standard Library Imports
import logging
import time
from typing import List, Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.term.business.model import QboTerm
from integrations.intuit.qbo.term.persistence.repo import QboTermRepository
from integrations.intuit.qbo.term.external.client import QboTermClient
from integrations.intuit.qbo.term.external.schemas import QboTerm as QboTermExternalSchema
from integrations.intuit.qbo.auth.business.service import QboAuthService
from shared.database import with_retry, is_transient_error

logger = logging.getLogger(__name__)

# Sync configuration
BATCH_SIZE = 10  # Process terms in batches
BATCH_DELAY = 0.5  # Delay between batches (seconds)
MAX_RETRIES = 3  # Max retries for transient errors
INITIAL_RETRY_DELAY = 2.0  # Initial retry delay (seconds)


class QboTermService:
    """
    Service for QboTerm entity business operations.
    """

    def __init__(self, repo: Optional[QboTermRepository] = None):
        """Initialize the QboTermService."""
        self.repo = repo or QboTermRepository()

    def sync_from_qbo(
        self,
        realm_id: str,
        last_updated_time: Optional[str] = None,
        sync_to_modules: bool = False
    ) -> List[QboTerm]:
        """
        Fetch Terms from QBO API and store locally.
        Uses upsert pattern: creates if not exists, updates if exists.
        
        Args:
            realm_id: QBO company realm ID
            last_updated_time: Optional ISO format datetime string. If provided, only fetches
                Terms where Metadata.LastUpdatedTime > last_updated_time.
            sync_to_modules: If True, also sync to PaymentTerm module
        
        Returns:
            List[QboTerm]: The synced term records
        """
        # Get valid access token
        auth_service = QboAuthService()
        qbo_auth = auth_service.ensure_valid_token(realm_id=realm_id)
        
        if not qbo_auth or not qbo_auth.access_token:
            raise ValueError(f"No valid access token found for realm_id: {realm_id}")
        
        # Fetch Terms from QBO API
        with QboTermClient(
            access_token=qbo_auth.access_token,
            realm_id=realm_id
        ) as client:
            qbo_terms: List[QboTermExternalSchema] = client.query_all_terms(
                last_updated_time=last_updated_time
            )
        
        if not qbo_terms:
            logger.info(f"No Terms found since {last_updated_time or 'beginning'}")
            return []
        
        logger.info(f"Retrieved {len(qbo_terms)} terms from QBO")
        
        # Process each term with retry logic and batch delays
        synced_terms = []
        failed_terms = []
        
        for i, qbo_term in enumerate(qbo_terms):
            try:
                # Use retry logic for transient database errors
                local_term = with_retry(
                    self._upsert_term,
                    qbo_term,
                    realm_id,
                    max_retries=MAX_RETRIES,
                    initial_delay=INITIAL_RETRY_DELAY,
                )
                synced_terms.append(local_term)
                logger.debug(f"Upserted term {qbo_term.id} ({i + 1}/{len(qbo_terms)})")
            except Exception as e:
                logger.error(f"Failed to upsert term {qbo_term.id}: {e}")
                failed_terms.append(qbo_term.id)
            
            # Add delay between batches to prevent connection exhaustion
            if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(qbo_terms):
                logger.debug(f"Processed {i + 1}/{len(qbo_terms)} terms, pausing...")
                time.sleep(BATCH_DELAY)
        
        if failed_terms:
            logger.warning(f"Failed to upsert {len(failed_terms)} terms: {failed_terms}")
        
        # Sync to modules if requested
        if sync_to_modules:
            self._sync_to_payment_terms(synced_terms)
        
        return synced_terms

    def _upsert_term(self, qbo_term: QboTermExternalSchema, realm_id: str) -> QboTerm:
        """
        Create or update a QboTerm record.
        
        Args:
            qbo_term: QBO Term from external API
            realm_id: QBO realm ID
        
        Returns:
            QboTerm: The created or updated record
        """
        # Check if term already exists
        existing = self.repo.read_by_qbo_id_and_realm_id(qbo_id=qbo_term.id, realm_id=realm_id)
        
        if existing:
            # Update existing record
            logger.debug(f"Updating existing QBO term {qbo_term.id}")
            return self.repo.update_by_qbo_id(
                qbo_id=qbo_term.id,
                row_version=existing.row_version_bytes,
                sync_token=qbo_term.sync_token,
                realm_id=realm_id,
                name=qbo_term.name,
                discount_percent=qbo_term.discount_percent,
                discount_days=qbo_term.discount_days,
                active=qbo_term.active,
                type=qbo_term.type,
                day_of_month_due=qbo_term.day_of_month_due,
                discount_day_of_month=qbo_term.discount_day_of_month,
                due_next_month_days=qbo_term.due_next_month_days,
                due_days=qbo_term.due_days,
            )
        else:
            # Create new record
            logger.debug(f"Creating new QBO term {qbo_term.id}")
            return self.repo.create(
                qbo_id=qbo_term.id,
                sync_token=qbo_term.sync_token,
                realm_id=realm_id,
                name=qbo_term.name,
                discount_percent=qbo_term.discount_percent,
                discount_days=qbo_term.discount_days,
                active=qbo_term.active,
                type=qbo_term.type,
                day_of_month_due=qbo_term.day_of_month_due,
                discount_day_of_month=qbo_term.discount_day_of_month,
                due_next_month_days=qbo_term.due_next_month_days,
                due_days=qbo_term.due_days,
            )

    def _sync_to_payment_terms(self, terms: List[QboTerm]) -> None:
        """
        Sync terms to PaymentTerm module.
        
        Args:
            terms: List of QboTerm records
        """
        if not terms:
            return
        
        # Import here to avoid circular dependencies
        from integrations.intuit.qbo.term.connector.payment_term.business.service import TermPaymentTermConnector
        
        connector = TermPaymentTermConnector()
        
        for term in terms:
            try:
                payment_term = connector.sync_from_qbo_term(term)
                logger.info(f"Synced QboTerm {term.id} to PaymentTerm {payment_term.id}")
            except Exception as e:
                logger.error(f"Failed to sync QboTerm {term.id} to PaymentTerm: {e}")

    def read_all(self) -> List[QboTerm]:
        """
        Read all QboTerms.
        """
        return self.repo.read_all()

    def read_by_realm_id(self, realm_id: str) -> List[QboTerm]:
        """
        Read all QboTerms by realm ID.
        """
        return self.repo.read_by_realm_id(realm_id)

    def read_by_qbo_id(self, qbo_id: str) -> Optional[QboTerm]:
        """
        Read a QboTerm by QBO ID.
        """
        return self.repo.read_by_qbo_id(qbo_id)

    def read_by_id(self, id: int) -> Optional[QboTerm]:
        """
        Read a QboTerm by database ID.
        """
        return self.repo.read_by_id(id)
