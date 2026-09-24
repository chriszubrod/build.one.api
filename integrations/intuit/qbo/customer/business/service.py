# Python Standard Library Imports
import itertools
import logging
from typing import List, Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.customer.business.model import QboCustomer
from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository
from integrations.intuit.qbo.customer.external.client import QboCustomerClient
from integrations.intuit.qbo.customer.external.schemas import QboCustomer as QboCustomerExternalSchema
from integrations.intuit.qbo.base.pacing import pace_batch
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome, project_records
from integrations.intuit.qbo.customer.connector.customer.business.service import CustomerCustomerConnector
from integrations.intuit.qbo.customer.connector.project.business.service import CustomerProjectConnector
from integrations.intuit.qbo.physical_address.business.service import QboPhysicalAddressService
from shared.database import with_retry

logger = logging.getLogger(__name__)

# Sync configuration
MAX_RETRIES = 3  # Max retries for transient errors
INITIAL_RETRY_DELAY = 2.0  # Initial retry delay (seconds)


class QboCustomerService:
    """
    Service for QboCustomer entity business operations.
    """

    def __init__(self, repo: Optional[QboCustomerRepository] = None):
        """Initialize the QboCustomerService."""
        self.repo = repo or QboCustomerRepository()
        self.physical_address_service = QboPhysicalAddressService()

    def sync_from_qbo(
        self,
        realm_id: str,
        last_updated_time: Optional[str] = None,
        sync_to_modules: bool = False
    ) -> SyncOutcome[QboCustomer]:
        """
        Fetch Customers from QBO API and store locally.
        Uses upsert pattern: creates if not exists, updates if exists.
        
        Args:
            realm_id: QBO company realm ID
            last_updated_time: Optional ISO format datetime string. If provided, only fetches
                Customers where Metadata.LastUpdatedTime > last_updated_time.
            sync_to_modules: If True, also sync to Customer/Project modules
        
        Returns:
            SyncOutcome[QboCustomer]: Pull run envelope including synced staging rows
        """
        outcome: SyncOutcome[QboCustomer] = SyncOutcome.for_service_pull()
        # Fetch Customers from QBO API. QboHttpClient (via QboCustomerClient) resolves
        # and refreshes the access token lazily, so no upfront auth call is needed.
        with QboCustomerClient(realm_id=realm_id) as client:
            qbo_customers: List[QboCustomerExternalSchema] = client.query_all_customers(
                last_updated_time=last_updated_time
            )
        
        outcome.fetched = len(qbo_customers)
        if not qbo_customers:
            logger.info(f"No Customers found since {last_updated_time or 'beginning'}")
            return outcome
        
        logger.info(f"Retrieved {len(qbo_customers)} customers from QBO")
        
        # Process each customer with retry logic and batch delays
        parent_customers = []
        job_customers = []
        # U-513: the EXTERNAL payload, keyed by QBO id, so projection can read
        # the addresses QBO already handed back INLINE (`BillAddr`) instead of
        # re-reading them out of the `qbo.PhysicalAddress` staging table this
        # loop just wrote them to. The staging rows are the thing being sunset;
        # the payload is where the data was all along, and this loop is the last
        # scope that still has it. See `_sync_to_customers` for how it reaches
        # the connector without touching the shared `project_records`.
        external_by_id = {}

        for i, qbo_customer in enumerate(qbo_customers):
            try:
                # Use retry logic for transient database errors
                local_customer = with_retry(
                    self._upsert_customer,
                    qbo_customer,
                    realm_id,
                    max_retries=MAX_RETRIES,
                    initial_delay=INITIAL_RETRY_DELAY,
                )
                outcome.record_synced(local_customer)
                logger.debug(f"Upserted customer {qbo_customer.id} ({i + 1}/{len(qbo_customers)})")

                # Categorize for module sync
                external_by_id[qbo_customer.id] = qbo_customer
                if qbo_customer.job:
                    job_customers.append(local_customer)
                else:
                    parent_customers.append(local_customer)
            except Exception as e:
                logger.error(f"Failed to upsert customer {qbo_customer.id}: {e}")
                outcome.record_staging_failure(qbo_customer.id, e)

            # Add delay between batches to prevent connection exhaustion
            pace_batch(i, len(qbo_customers), logger, "customers")

        if outcome.staging_failed_ids:
            logger.warning(
                f"Failed to upsert {len(outcome.staging_failed_ids)} customers: {outcome.staging_failed_ids}"
            )

        # Sync to modules if requested. Parents FIRST, jobs second — a job's
        # billing fallback reads the `dbo.Address` its parent's projection mints
        # (U-513), so this ordering is load-bearing, not cosmetic.
        if sync_to_modules:
            self._sync_to_customers(parent_customers, outcome, external_by_id)
            self._sync_to_projects(job_customers, outcome)
        
        return outcome

    def _upsert_customer(self, qbo_customer: QboCustomerExternalSchema, realm_id: str) -> QboCustomer:
        """
        Create or update a QboCustomer record.
        
        Args:
            qbo_customer: QBO Customer from external API
            realm_id: QBO realm ID
        
        Returns:
            QboCustomer: The created or updated record
        """
        if not qbo_customer.id:
            raise ValueError("QBO Customer must have an ID")

        # Check if customer already exists
        existing = self.repo.read_by_qbo_id_and_realm_id(qbo_id=qbo_customer.id, realm_id=realm_id)
        
        # Extract reference values
        parent_ref_value = qbo_customer.parent_ref.value if qbo_customer.parent_ref else None
        parent_ref_name = qbo_customer.parent_ref.name if qbo_customer.parent_ref else None
        
        # Extract email
        primary_email_addr = qbo_customer.primary_email_addr.address if qbo_customer.primary_email_addr else None
        
        # Extract phone numbers
        primary_phone = qbo_customer.primary_phone.free_form_number if qbo_customer.primary_phone else None
        mobile = qbo_customer.mobile.free_form_number if qbo_customer.mobile else None
        fax = qbo_customer.fax.free_form_number if qbo_customer.fax else None
        
        # Create/update bill address and get ID
        bill_addr_id = None
        if qbo_customer.bill_addr:
            bill_addr_id = self._upsert_physical_address(
                qbo_address=qbo_customer.bill_addr,
                qbo_id=f"{qbo_customer.id}_bill",
                realm_id=realm_id
            )
        
        # Create/update ship address and get ID
        ship_addr_id = None
        if qbo_customer.ship_addr:
            ship_addr_id = self._upsert_physical_address(
                qbo_address=qbo_customer.ship_addr,
                qbo_id=f"{qbo_customer.id}_ship",
                realm_id=realm_id
            )
        
        if existing:
            # Update existing record
            logger.debug(f"Updating existing QBO customer {qbo_customer.id}")
            return self.repo.update_by_qbo_id(
                qbo_id=qbo_customer.id,
                row_version=existing.row_version_bytes,
                sync_token=qbo_customer.sync_token,
                realm_id=realm_id,
                display_name=qbo_customer.display_name,
                title=qbo_customer.title,
                given_name=qbo_customer.given_name,
                middle_name=qbo_customer.middle_name,
                family_name=qbo_customer.family_name,
                suffix=qbo_customer.suffix,
                company_name=qbo_customer.company_name,
                fully_qualified_name=qbo_customer.fully_qualified_name,
                level=qbo_customer.level,
                parent_ref_value=parent_ref_value,
                parent_ref_name=parent_ref_name,
                job=qbo_customer.job,
                active=qbo_customer.active,
                primary_email_addr=primary_email_addr,
                primary_phone=primary_phone,
                mobile=mobile,
                fax=fax,
                bill_addr_id=bill_addr_id,
                ship_addr_id=ship_addr_id,
                balance=qbo_customer.balance,
                balance_with_jobs=qbo_customer.balance_with_jobs,
                taxable=qbo_customer.taxable,
                notes=qbo_customer.notes,
                print_on_check_name=qbo_customer.print_on_check_name,
            )
        else:
            # Create new record
            logger.debug(f"Creating new QBO customer {qbo_customer.id}")
            return self.repo.create(
                qbo_id=qbo_customer.id,
                sync_token=qbo_customer.sync_token,
                realm_id=realm_id,
                display_name=qbo_customer.display_name,
                title=qbo_customer.title,
                given_name=qbo_customer.given_name,
                middle_name=qbo_customer.middle_name,
                family_name=qbo_customer.family_name,
                suffix=qbo_customer.suffix,
                company_name=qbo_customer.company_name,
                fully_qualified_name=qbo_customer.fully_qualified_name,
                level=qbo_customer.level,
                parent_ref_value=parent_ref_value,
                parent_ref_name=parent_ref_name,
                job=qbo_customer.job,
                active=qbo_customer.active,
                primary_email_addr=primary_email_addr,
                primary_phone=primary_phone,
                mobile=mobile,
                fax=fax,
                bill_addr_id=bill_addr_id,
                ship_addr_id=ship_addr_id,
                balance=qbo_customer.balance,
                balance_with_jobs=qbo_customer.balance_with_jobs,
                taxable=qbo_customer.taxable,
                notes=qbo_customer.notes,
                print_on_check_name=qbo_customer.print_on_check_name,
            )

    def _sync_to_customers(
        self,
        parent_customers: List[QboCustomer],
        outcome: SyncOutcome,
        external_by_id: Optional[dict] = None,
    ) -> None:
        """
        Sync parent customers to Customer module.

        Args:
            parent_customers: List of parent QboCustomer records (Job=false)
            outcome: the pull envelope this projection records into
            external_by_id: QBO id -> the ORIGINAL external Customer payload
                (U-513). Optional; an absent/partial map just means the
                connector falls back to reading the staging row.

        The closure is the whole point: `project_records` is shared by 10 call
        sites across 8 other QBO families and takes a strict one-argument
        `project_one`, so binding the extra arguments HERE is what lets this
        family pass the payload through — and wrap the call in retry + pacing —
        without changing a signature every other family depends on.

        Retry + pacing (U-513 ph2): `scripts/sync_qbo_customer.py` used to run
        its own projection loop purely to get these two, which is why it could
        not simply ask for `sync_to_modules=True`. They belong here, alongside
        the identical pair the STAGING loop above already has — projection is
        per-row DB work too, and under load it is exactly what drops the TCP
        connection. `pace_batch` is in a `finally` so a row whose projection
        RAISED still paces: the next row's retry is what most needs the gap.
        The index comes from a counter in this closure, never from
        `project_records` — that seam stays one-argument for its 10 callers.
        """
        if not parent_customers:
            return

        connector = CustomerCustomerConnector()
        external_by_id = external_by_id or {}
        total = len(parent_customers)
        counter = itertools.count()

        def _project(row: QboCustomer):
            index = next(counter)
            try:
                return with_retry(
                    connector.sync_from_qbo_customer,
                    row,
                    external_by_id.get(row.qbo_id),
                    max_retries=MAX_RETRIES,
                    initial_delay=INITIAL_RETRY_DELAY,
                )
            finally:
                pace_batch(index, total, logger, "parent customers")

        project_records(
            parent_customers,
            outcome,
            label="Customer->Customer",
            project_one=_project,
            logger=logger,
        )

    def _sync_to_projects(self, job_customers: List[QboCustomer], outcome: SyncOutcome) -> None:
        """
        Sync job customers to Project module.

        Args:
            job_customers: List of job QboCustomer records (Job=true)

        No external payload here, deliberately: `CustomerProjectConnector` reads
        a job's billing address out of `dbo.Address` (the row the PARENT's
        projection just minted), not out of the QBO payload, so there is nothing
        for a second argument to carry. The closure exists only to wrap the call
        in retry + pacing — see `_sync_to_customers` for why both live at this
        call site rather than inside `project_records`.
        """
        if not job_customers:
            return

        connector = CustomerProjectConnector()
        total = len(job_customers)
        counter = itertools.count()

        def _project(row: QboCustomer):
            index = next(counter)
            try:
                return with_retry(
                    connector.sync_from_qbo_customer,
                    row,
                    max_retries=MAX_RETRIES,
                    initial_delay=INITIAL_RETRY_DELAY,
                )
            finally:
                pace_batch(index, total, logger, "job customers")

        project_records(
            job_customers,
            outcome,
            label="Customer->Project",
            project_one=_project,
            logger=logger,
        )

    def read_all(self) -> List[QboCustomer]:
        """
        Read all QboCustomers.
        """
        return self.repo.read_all()

    def read_by_realm_id(self, realm_id: str) -> List[QboCustomer]:
        """
        Read all QboCustomers by realm ID.
        """
        return self.repo.read_by_realm_id(realm_id)

    def read_by_qbo_id(self, qbo_id: str) -> Optional[QboCustomer]:
        """
        Read a QboCustomer by QBO ID.
        """
        return self.repo.read_by_qbo_id(qbo_id)

    def read_by_id(self, id: int) -> Optional[QboCustomer]:
        """
        Read a QboCustomer by database ID.
        """
        return self.repo.read_by_id(id)

    def _upsert_physical_address(
        self,
        qbo_address,
        qbo_id: str,
        realm_id: str
    ) -> Optional[int]:
        """
        Create or update a QboPhysicalAddress record and return its ID.
        
        Args:
            qbo_address: QboPhysicalAddress from external API
            qbo_id: QBO ID to use for the address record
            realm_id: QBO realm ID
        
        Returns:
            int: The database ID of the PhysicalAddress record, or None if address is empty
        """
        if not qbo_address:
            return None
        
        # Check if address already exists
        existing = self.physical_address_service.read_by_qbo_id(qbo_id=qbo_id)
        
        if existing:
            # Update existing record
            logger.debug(f"Updating existing QBO physical address {qbo_id}")
            updated = self.physical_address_service.repo.update_by_id(
                id=existing.id,
                row_version=existing.row_version_bytes,
                qbo_id=qbo_id,
                realm_id=realm_id,
                line1=qbo_address.line1,
                line2=qbo_address.line2,
                city=qbo_address.city,
                country=qbo_address.country,
                country_sub_division_code=qbo_address.country_sub_division_code,
                postal_code=qbo_address.postal_code,
            )
            return updated.id if updated else None
        else:
            # Create new record
            logger.debug(f"Creating new QBO physical address {qbo_id}")
            created = self.physical_address_service.create(
                qbo_id=qbo_id,
                realm_id=realm_id,
                line1=qbo_address.line1,
                line2=qbo_address.line2,
                city=qbo_address.city,
                country=qbo_address.country,
                country_sub_division_code=qbo_address.country_sub_division_code,
                postal_code=qbo_address.postal_code,
            )
            return created.id if created else None
