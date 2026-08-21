# Python Standard Library Imports
import logging
from typing import List, Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.account.business.model import QboAccount
from integrations.intuit.qbo.account.persistence.repo import QboAccountRepository
from integrations.intuit.qbo.account.external.client import QboAccountClient
from integrations.intuit.qbo.account.external.schemas import QboAccount as QboAccountExternalSchema
from integrations.intuit.qbo.base.pacing import pace_batch
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome
from entities.company.business.service import CompanyService
from shared.database import with_retry

logger = logging.getLogger(__name__)

# Sync configuration
MAX_RETRIES = 3  # Max retries for transient errors
INITIAL_RETRY_DELAY = 2.0  # Initial retry delay (seconds)

AP_ACCOUNT_TYPE = "Accounts Payable"


def select_ap_account(accounts: List[QboAccount]) -> Optional[QboAccount]:
    """
    Pick "the" Accounts Payable account for a realm from its local qbo.Account
    mirror — first AccountType match, in whatever order `accounts` is given
    (callers pass `read_by_realm_id`'s result, which is `Name ASC`).

    Shared (U-281) by the post-pull AP-account cache derivation below
    (`QboAccountService._sync_ap_account_cache`) and
    `BillBillConnector._get_ap_account_ref`'s live-scan fallback, so the two
    can never silently diverge on which account "the" AP account is — this
    used to be BillBillConnector's own inline loop before the repoint.
    """
    for account in accounts:
        if account.account_type == AP_ACCOUNT_TYPE:
            return account
    return None


class QboAccountService:
    """
    Service for QboAccount entity business operations.
    """

    def __init__(
        self,
        repo: Optional[QboAccountRepository] = None,
        company_service: Optional[CompanyService] = None,
    ):
        """Initialize the QboAccountService."""
        self.repo = repo or QboAccountRepository()
        self.company_service = company_service or CompanyService()

    def sync_from_qbo(
        self,
        realm_id: str,
        last_updated_time: Optional[str] = None,
    ) -> SyncOutcome[QboAccount]:
        """
        Fetch Accounts from QBO API and store locally.
        Uses upsert pattern: creates if not exists, updates if exists.

        Args:
            realm_id: QBO company realm ID
            last_updated_time: Optional ISO format datetime string. If provided, only fetches
                Accounts where Metadata.LastUpdatedTime > last_updated_time.
        Returns:
            SyncOutcome[QboAccount]: Pull run envelope including synced staging rows
        """
        outcome: SyncOutcome[QboAccount] = SyncOutcome.for_service_pull()
        self._realm_id = realm_id

        # Fetch Accounts from QBO API. QboHttpClient (via QboAccountClient) resolves
        # and refreshes the access token lazily, so no upfront auth call is needed.
        with QboAccountClient(realm_id=realm_id) as client:
            qbo_accounts: List[QboAccountExternalSchema] = client.query_all_accounts(
                last_updated_time=last_updated_time
            )

        outcome.fetched = len(qbo_accounts)
        if not qbo_accounts:
            logger.info(f"No Accounts found since {last_updated_time or 'beginning'}")
            return outcome

        logger.info(f"Retrieved {len(qbo_accounts)} accounts from QBO")

        # Process each account with retry logic and batch delays
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
                outcome.record_synced(local_account)

                # Track deactivated accounts from QBO
                if qbo_account.active is False:
                    deactivated_count += 1
                    logger.info(f"Account {qbo_account.id} ({qbo_account.name}) is deactivated in QBO")

                logger.debug(f"Upserted account {qbo_account.id} ({i + 1}/{len(qbo_accounts)})")
            except Exception as e:
                logger.error(f"Failed to upsert account {qbo_account.id}: {e}")
                outcome.record_staging_failure(qbo_account.id, e)

            # Add delay between batches to prevent connection exhaustion.
            # Token refresh is handled automatically by QboHttpClient on each request.
            pace_batch(i, len(qbo_accounts), logger, "accounts")

        if deactivated_count:
            logger.info(f"{deactivated_count} accounts are deactivated in QBO (Active=false synced locally)")

        if outcome.staging_failed_ids:
            logger.warning(
                f"Failed to upsert {len(outcome.staging_failed_ids)} accounts: {outcome.staging_failed_ids}"
            )

        # Delete-reconcile retired (U-218c): U-219 wide pull mirrors QBO Active directly;
        # QBO cannot hard-delete Accounts, so absent-from-response deactivation was a no-op
        # that could mass-deactivate hidden inactive staging rows if ever armed.

        self._sync_ap_account_cache(realm_id)

        return outcome

    def _sync_ap_account_cache(self, realm_id: str) -> None:
        """
        Re-derive "the" Accounts Payable account for this realm (U-281) and
        cache it on dbo.Company, so BillBillConnector._get_ap_account_ref no
        longer has to scan qbo.Account on every live Bill push.

        Re-queries the FULL local qbo.Account mirror rather than just the
        `qbo_accounts` batch this call fetched — an incremental pull
        (`last_updated_time` set) only returns rows that changed, so the AP
        account itself may not be in this particular batch even though it's
        already staged from an earlier pull. Re-deriving from the full
        mirror every time keeps this correct regardless of pull shape.

        Skips the write entirely when the derived value already matches
        what's cached — the overwhelmingly common case on any pull that
        didn't touch the AP account itself. `SetCompanyApAccount`'s UPDATE
        bumps `dbo.Company.RowVersion` as an inherent side effect (any
        SQL Server ROWVERSION column advances on any UPDATE to the row,
        regardless of which columns changed), so a no-op write isn't free —
        it's a needless RowVersion churn on a row other code paths hold
        optimistic-concurrency tokens against.

        Failure-isolated: a Company-side read/write problem must not fail
        the account pull it rides on.
        """
        try:
            accounts = self.repo.read_by_realm_id(realm_id)
            ap_account = select_ap_account(accounts)
            qbo_id = ap_account.qbo_id if ap_account else None
            name = ap_account.name if ap_account else None

            existing = self.company_service.read_by_realm_id(realm_id)
            if existing and existing.ap_account_qbo_id == qbo_id and existing.ap_account_name == name:
                return

            self.company_service.set_ap_account(
                realm_id=realm_id, ap_account_qbo_id=qbo_id, ap_account_name=name
            )
        except Exception as e:
            logger.error(f"Failed to cache AP account for realm_id {realm_id}: {e}")

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
