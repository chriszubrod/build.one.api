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
        reconcile_deletes: bool = False,
    ) -> List[QboAccount]:
        """
        Fetch Accounts from QBO API and store locally.
        Uses upsert pattern: creates if not exists, updates if exists.

        Args:
            realm_id: QBO company realm ID
            last_updated_time: Optional ISO format datetime string. If provided, only fetches
                Accounts where Metadata.LastUpdatedTime > last_updated_time.
            reconcile_deletes: If True, deactivate local records that no longer exist in QBO.
                Only runs on full sync (when last_updated_time is None).

        Returns:
            List[QboAccount]: The synced account records
        """
        self._realm_id = realm_id

        # Fetch Accounts from QBO API. QboHttpClient (via QboAccountClient) resolves
        # and refreshes the access token lazily, so no upfront auth call is needed.
        with QboAccountClient(realm_id=realm_id) as client:
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
        deactivated_count = 0

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

                # Track deactivated accounts from QBO
                if qbo_account.active is False:
                    deactivated_count += 1
                    logger.info(f"Account {qbo_account.id} ({qbo_account.name}) is deactivated in QBO")

                logger.debug(f"Upserted account {qbo_account.id} ({i + 1}/{len(qbo_accounts)})")
            except Exception as e:
                logger.error(f"Failed to upsert account {qbo_account.id}: {e}")
                failed_accounts.append(qbo_account.id)

            # Add delay between batches to prevent connection exhaustion.
            # Token refresh is handled automatically by QboHttpClient on each request.
            if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(qbo_accounts):
                logger.debug(f"Processed {i + 1}/{len(qbo_accounts)} accounts, pausing...")
                time.sleep(BATCH_DELAY)

        if deactivated_count:
            logger.info(f"{deactivated_count} accounts are deactivated in QBO (Active=false synced locally)")

        if failed_accounts:
            logger.warning(f"Failed to upsert {len(failed_accounts)} accounts: {failed_accounts}")

        # Reconcile deletes: find local accounts that no longer exist in QBO
        # Only runs on full sync to avoid false positives from incremental queries
        if reconcile_deletes and last_updated_time is None:
            self._reconcile_deleted_accounts(realm_id, qbo_accounts)

        return synced_accounts

    def _reconcile_deleted_accounts(
        self,
        realm_id: str,
        qbo_accounts: List[QboAccountExternalSchema],
    ) -> int:
        """
        Find local QboAccount records that no longer exist in QBO and deactivate them.

        Compares local records against the full QBO account list. Any local record
        whose QboId is not in the QBO response is marked Active=False.

        Args:
            realm_id: QBO realm ID
            qbo_accounts: Complete list of accounts from QBO (must be a full sync, not incremental)

        Returns:
            int: Number of accounts deactivated locally
        """
        # Build set of QBO IDs from the API response
        qbo_ids_from_api = {acc.id for acc in qbo_accounts}

        # Get all local accounts for this realm
        local_accounts = self.repo.read_by_realm_id(realm_id)

        deactivated = 0
        for local in local_accounts:
            if local.qbo_id not in qbo_ids_from_api and local.active is not False:
                logger.warning(
                    f"Local QBO account {local.qbo_id} ({local.name}) not found in QBO — "
                    f"marking as inactive (likely deleted in QBO)"
                )
                try:
                    self.repo.update_by_qbo_id(
                        qbo_id=local.qbo_id,
                        row_version=local.row_version_bytes,
                        sync_token=local.sync_token,
                        realm_id=realm_id,
                        name=local.name,
                        acct_num=local.acct_num,
                        description=local.description,
                        active=False,
                        classification=local.classification,
                        account_type=local.account_type,
                        account_sub_type=local.account_sub_type,
                        fully_qualified_name=local.fully_qualified_name,
                        sub_account=local.sub_account,
                        parent_ref_value=local.parent_ref_value,
                        parent_ref_name=local.parent_ref_name,
                        current_balance=local.current_balance,
                        current_balance_with_sub_accounts=local.current_balance_with_sub_accounts,
                        currency_ref_value=local.currency_ref_value,
                        currency_ref_name=local.currency_ref_name,
                    )
                    deactivated += 1
                except Exception as e:
                    logger.error(f"Failed to deactivate local account {local.qbo_id}: {e}")

        if deactivated:
            logger.info(f"Deactivated {deactivated} local accounts not found in QBO")

        return deactivated

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
            updated = self.repo.update_by_qbo_id(
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
            if updated is None:
                # RowVersion conflict — re-read fresh and retry once
                logger.warning(f"RowVersion conflict updating QBO account {qbo_account.id}, retrying with fresh row_version")
                refreshed = self.repo.read_by_qbo_id_and_realm_id(qbo_id=qbo_account.id, realm_id=realm_id)
                if not refreshed:
                    raise ValueError(f"QBO account {qbo_account.id} disappeared during update retry")
                updated = self.repo.update_by_qbo_id(
                    qbo_id=qbo_account.id,
                    row_version=refreshed.row_version_bytes,
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
                if updated is None:
                    raise ValueError(f"Failed to update QBO account {qbo_account.id} after RowVersion retry")
            return updated
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
