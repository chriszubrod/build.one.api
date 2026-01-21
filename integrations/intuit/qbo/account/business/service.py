# Python Standard Library Imports
import logging
import time
from typing import List, Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.account.business.model import QboAccount
from integrations.intuit.qbo.account.persistence.repo import QboAccountRepository
from integrations.intuit.qbo.account.external.client import QboAccountClient
from integrations.intuit.qbo.account.external.schemas import QboAccount as QboAccountExternalSchema
from integrations.intuit.qbo.auth.business.service import QboAuthService
from shared.database import with_retry

logger = logging.getLogger(__name__)

# Sync configuration
BATCH_SIZE = 10  # Process accounts in batches
BATCH_DELAY = 0.5  # Delay between batches (seconds)
MAX_RETRIES = 3  # Max retries for transient errors
INITIAL_RETRY_DELAY = 2.0  # Initial retry delay (seconds)


class QboAccountService:
    """
    Service for QboAccount entity business operations.
    """

    def __init__(self, repo: Optional[QboAccountRepository] = None):
        """Initialize the QboAccountService."""
        self.repo = repo or QboAccountRepository()

    def sync_from_qbo(
        self,
        realm_id: str,
        last_updated_time: Optional[str] = None,
    ) -> List[QboAccount]:
        """
        Fetch Accounts from QBO API and store locally.
        Uses upsert pattern: creates if not exists, updates if exists.
        
        Args:
            realm_id: QBO company realm ID
            last_updated_time: Optional ISO format datetime string. If provided, only fetches
                Accounts where Metadata.LastUpdatedTime > last_updated_time.
        
        Returns:
            List[QboAccount]: The synced account records
        """
        # Get valid access token
        auth_service = QboAuthService()
        qbo_auth = auth_service.ensure_valid_token(realm_id=realm_id)
        
        if not qbo_auth or not qbo_auth.access_token:
            raise ValueError(f"No valid access token found for realm_id: {realm_id}")
        
        # Fetch Accounts from QBO API
        with QboAccountClient(
            access_token=qbo_auth.access_token,
            realm_id=realm_id
        ) as client:
            qbo_accounts: List[QboAccountExternalSchema] = client.query_all_accounts(
                last_updated_time=last_updated_time
            )
        
        if not qbo_accounts:
            logger.info(f"No Accounts found since {last_updated_time or 'beginning'}")
            return []
        
        logger.info(f"Retrieved {len(qbo_accounts)} accounts from QBO")
        
        # Process each account with retry logic and batch delays
        synced_accounts = []
        failed_accounts = []
        
        for i, qbo_account in enumerate(qbo_accounts):
            try:
                # Use retry logic for transient database errors
                local_account = with_retry(
                    self._upsert_account,
                    qbo_account,
                    realm_id,
                    max_retries=MAX_RETRIES,
                    initial_delay=INITIAL_RETRY_DELAY,
                )
                synced_accounts.append(local_account)
                logger.debug(f"Upserted account {qbo_account.id} ({i + 1}/{len(qbo_accounts)})")
            except Exception as e:
                logger.error(f"Failed to upsert account {qbo_account.id}: {e}")
                failed_accounts.append(qbo_account.id)
            
            # Add delay between batches to prevent connection exhaustion
            if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(qbo_accounts):
                logger.debug(f"Processed {i + 1}/{len(qbo_accounts)} accounts, pausing...")
                time.sleep(BATCH_DELAY)
        
        if failed_accounts:
            logger.warning(f"Failed to upsert {len(failed_accounts)} accounts: {failed_accounts}")
        
        return synced_accounts

    def _upsert_account(self, qbo_account: QboAccountExternalSchema, realm_id: str) -> QboAccount:
        """
        Create or update a QboAccount record.
        
        Args:
            qbo_account: QBO Account from external API
            realm_id: QBO realm ID
        
        Returns:
            QboAccount: The created or updated record
        """
        # Check if account already exists
        existing = self.repo.read_by_qbo_id_and_realm_id(qbo_id=qbo_account.id, realm_id=realm_id)
        
        # Extract parent reference
        parent_ref_value = None
        parent_ref_name = None
        if qbo_account.parent_ref:
            parent_ref_value = qbo_account.parent_ref.value
            parent_ref_name = qbo_account.parent_ref.name
        
        # Extract currency reference
        currency_ref_value = None
        currency_ref_name = None
        if qbo_account.currency_ref:
            currency_ref_value = qbo_account.currency_ref.value
            currency_ref_name = qbo_account.currency_ref.name
        
        if existing:
            # Update existing record
            logger.debug(f"Updating existing QBO account {qbo_account.id}")
            return self.repo.update_by_qbo_id(
                qbo_id=qbo_account.id,
                row_version=existing.row_version_bytes,
                sync_token=qbo_account.sync_token,
                realm_id=realm_id,
                name=qbo_account.name,
                acct_num=qbo_account.acct_num,
                description=qbo_account.description,
                active=qbo_account.active,
                classification=qbo_account.classification,
                account_type=qbo_account.account_type,
                account_sub_type=qbo_account.account_sub_type,
                fully_qualified_name=qbo_account.fully_qualified_name,
                sub_account=qbo_account.sub_account,
                parent_ref_value=parent_ref_value,
                parent_ref_name=parent_ref_name,
                current_balance=qbo_account.current_balance,
                current_balance_with_sub_accounts=qbo_account.current_balance_with_sub_accounts,
                currency_ref_value=currency_ref_value,
                currency_ref_name=currency_ref_name,
            )
        else:
            # Create new record
            logger.debug(f"Creating new QBO account {qbo_account.id}")
            return self.repo.create(
                qbo_id=qbo_account.id,
                sync_token=qbo_account.sync_token,
                realm_id=realm_id,
                name=qbo_account.name,
                acct_num=qbo_account.acct_num,
                description=qbo_account.description,
                active=qbo_account.active,
                classification=qbo_account.classification,
                account_type=qbo_account.account_type,
                account_sub_type=qbo_account.account_sub_type,
                fully_qualified_name=qbo_account.fully_qualified_name,
                sub_account=qbo_account.sub_account,
                parent_ref_value=parent_ref_value,
                parent_ref_name=parent_ref_name,
                current_balance=qbo_account.current_balance,
                current_balance_with_sub_accounts=qbo_account.current_balance_with_sub_accounts,
                currency_ref_value=currency_ref_value,
                currency_ref_name=currency_ref_name,
            )

    def read_all(self) -> List[QboAccount]:
        """
        Read all QboAccounts.
        """
        return self.repo.read_all()

    def read_by_realm_id(self, realm_id: str) -> List[QboAccount]:
        """
        Read all QboAccounts by realm ID.
        """
        return self.repo.read_by_realm_id(realm_id)

    def read_by_qbo_id(self, qbo_id: str) -> Optional[QboAccount]:
        """
        Read a QboAccount by QBO ID.
        """
        return self.repo.read_by_qbo_id(qbo_id)

    def read_by_id(self, id: int) -> Optional[QboAccount]:
        """
        Read a QboAccount by database ID.
        """
        return self.repo.read_by_id(id)
