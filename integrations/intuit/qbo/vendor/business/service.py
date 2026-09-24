# Python Standard Library Imports
import itertools
import logging
from typing import Dict, List, Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.vendor.business.model import QboVendor
from integrations.intuit.qbo.vendor.persistence.repo import QboVendorRepository
from integrations.intuit.qbo.vendor.external.client import QboVendorClient
from integrations.intuit.qbo.vendor.external.schemas import QboVendor as QboVendorExternalSchema
from integrations.intuit.qbo.base.pacing import pace_batch
from integrations.intuit.qbo.base.sync_outcome import SyncOutcome, project_records
from shared.database import with_retry, is_transient_error

logger = logging.getLogger(__name__)

# Sync configuration
MAX_RETRIES = 3  # Max retries for transient errors
INITIAL_RETRY_DELAY = 2.0  # Initial retry delay (seconds)


class QboVendorService:
    """
    Service for QboVendor entity business operations.
    """

    def __init__(self, repo: Optional[QboVendorRepository] = None):
        """Initialize the QboVendorService."""
        self.repo = repo or QboVendorRepository()

    def sync_from_qbo(
        self,
        realm_id: str,
        last_updated_time: Optional[str] = None,
        sync_to_modules: bool = False,
    ) -> SyncOutcome[QboVendor]:
        """
        Fetch Vendors from QBO API and store locally.
        Uses upsert pattern: creates if not exists, updates if exists.
        
        Args:
            realm_id: QBO company realm ID
            last_updated_time: Optional ISO format datetime string. If provided, only fetches
                Vendors where Metadata.LastUpdatedTime > last_updated_time.
            sync_to_modules: If True, also sync to Vendor/VendorAddress modules
        
        Returns:
            SyncOutcome[QboVendor]: Pull run envelope including synced staging rows
        """
        outcome: SyncOutcome[QboVendor] = SyncOutcome.for_service_pull()
        # Fetch Vendors from QBO API. QboHttpClient (via QboVendorClient) resolves
        # and refreshes the access token lazily, so no upfront auth call is needed.
        with QboVendorClient(realm_id=realm_id) as client:
            qbo_vendors: List[QboVendorExternalSchema] = client.query_all_vendors(
                last_updated_time=last_updated_time
            )

        outcome.fetched = len(qbo_vendors)
        if not qbo_vendors:
            logger.info(f"No Vendors found since {last_updated_time or 'beginning'}")
            return outcome
        
        logger.info(f"Retrieved {len(qbo_vendors)} vendors from QBO")
        
        # Process each vendor with retry logic and batch delays

        # U-513: keep each EXTERNAL record alongside its staging upsert, keyed by
        # QBO id, so the projection below can read the vendor's address off the
        # inline `BillAddr` payload instead of reading `qbo.PhysicalAddress` back.
        external_by_id: Dict[str, QboVendorExternalSchema] = {}

        for i, qbo_vendor in enumerate(qbo_vendors):
            try:
                # Use retry logic for transient database errors
                local_vendor = with_retry(
                    self._upsert_vendor,
                    qbo_vendor,
                    realm_id,
                    max_retries=MAX_RETRIES,
                    initial_delay=INITIAL_RETRY_DELAY,
                )
                outcome.record_synced(local_vendor)
                # Recorded only on a successful staging upsert, and only under this
                # record's OWN id -- the pairing the projection closure relies on.
                external_by_id[str(qbo_vendor.id)] = qbo_vendor
                logger.debug(f"Upserted vendor {qbo_vendor.id} ({i + 1}/{len(qbo_vendors)})")
            except Exception as e:
                logger.error(f"Failed to upsert vendor {qbo_vendor.id}: {e}")
                outcome.record_staging_failure(qbo_vendor.id, e)
            
            # Add delay between batches to prevent connection exhaustion
            pace_batch(i, len(qbo_vendors), logger, "vendors")
        
        if outcome.staging_failed_ids:
            logger.warning(
                f"Failed to upsert {len(outcome.staging_failed_ids)} vendors: {outcome.staging_failed_ids}"
            )
        
        # Sync to modules if requested
        if sync_to_modules:
            self._sync_to_vendors(outcome.synced, outcome, external_by_id)

        return outcome

    def _upsert_vendor(self, qbo_vendor: QboVendorExternalSchema, realm_id: str) -> QboVendor:
        """
        Create or update a QboVendor record.
        
        Args:
            qbo_vendor: QBO Vendor from external API
            realm_id: QBO realm ID
        
        Returns:
            QboVendor: The created or updated record
        """
        if not qbo_vendor.id:
            raise ValueError("QBO Vendor must have an ID")

        # Check if vendor already exists
        existing = self.repo.read_by_qbo_id_and_realm_id(qbo_id=qbo_vendor.id, realm_id=realm_id)
        
        # Extract email
        primary_email_addr = qbo_vendor.primary_email_addr.address if qbo_vendor.primary_email_addr else None
        
        # Extract phone numbers
        primary_phone = qbo_vendor.primary_phone.free_form_number if qbo_vendor.primary_phone else None
        mobile = qbo_vendor.mobile.free_form_number if qbo_vendor.mobile else None
        fax = qbo_vendor.fax.free_form_number if qbo_vendor.fax else None
        
        # Extract web address
        web_addr = None
        if qbo_vendor.web_addr:
            web_addr = qbo_vendor.web_addr.get("URI") if isinstance(qbo_vendor.web_addr, dict) else str(qbo_vendor.web_addr)
        
        # U-513 ph3a: the `qbo.PhysicalAddress` staging WRITE is gone. Phases 1-2
        # moved the projection onto the vendor's INLINE `BillAddr` payload
        # (`VendorVendorConnector._bill_address_from_payload`), which made this a
        # write-then-never-read-back cache; ph3b drops the table, its sprocs and
        # the `BillAddrId` column. The `bill_addr_id=` keyword stays on both repo
        # calls -- the repo/sproc parameter is NOT being removed here, precisely
        # so this deploy is compatible in both directions with the container it
        # replaces. It is now always NULL:
        #   * CREATE inserts NULL.
        #   * UPDATE preserves whatever the row already holds -- `UpdateQboVendor
        #     ByQboId` guards every column with `CASE WHEN @X IS NULL THEN [X]`.
        #     That is deliberate: a container still running the phase-2 code can
        #     keep resolving an existing vendor's address through the staging
        #     row while the rollout is in flight.
        # `qbo_vendor.bill_addr` is deliberately no longer read here; the
        # connector reads it off the same external record it is handed.
        if existing:
            # Update existing record
            logger.debug(f"Updating existing QBO vendor {qbo_vendor.id}")
            return self.repo.update_by_qbo_id(
                qbo_id=qbo_vendor.id,
                row_version=existing.row_version_bytes,
                sync_token=qbo_vendor.sync_token,
                realm_id=realm_id,
                display_name=qbo_vendor.display_name,
                title=qbo_vendor.title,
                given_name=qbo_vendor.given_name,
                middle_name=qbo_vendor.middle_name,
                family_name=qbo_vendor.family_name,
                suffix=qbo_vendor.suffix,
                company_name=qbo_vendor.company_name,
                print_on_check_name=qbo_vendor.print_on_check_name,
                tax_identifier=qbo_vendor.tax_identifier,
                vendor_1099=qbo_vendor.vendor_1099,
                active=qbo_vendor.active,
                primary_email_addr=primary_email_addr,
                primary_phone=primary_phone,
                mobile=mobile,
                fax=fax,
                bill_addr_id=None,  # U-513 ph3a -- see _upsert_vendor's note above
                balance=qbo_vendor.balance,
                acct_num=qbo_vendor.acct_num,
                web_addr=web_addr,
            )
        else:
            # Create new record
            logger.debug(f"Creating new QBO vendor {qbo_vendor.id}")
            return self.repo.create(
                qbo_id=qbo_vendor.id,
                sync_token=qbo_vendor.sync_token,
                realm_id=realm_id,
                display_name=qbo_vendor.display_name,
                title=qbo_vendor.title,
                given_name=qbo_vendor.given_name,
                middle_name=qbo_vendor.middle_name,
                family_name=qbo_vendor.family_name,
                suffix=qbo_vendor.suffix,
                company_name=qbo_vendor.company_name,
                print_on_check_name=qbo_vendor.print_on_check_name,
                tax_identifier=qbo_vendor.tax_identifier,
                vendor_1099=qbo_vendor.vendor_1099,
                active=qbo_vendor.active,
                primary_email_addr=primary_email_addr,
                primary_phone=primary_phone,
                mobile=mobile,
                fax=fax,
                bill_addr_id=None,  # U-513 ph3a -- see _upsert_vendor's note above
                balance=qbo_vendor.balance,
                acct_num=qbo_vendor.acct_num,
                web_addr=web_addr,
            )

    def _sync_to_vendors(
        self,
        vendors: List[QboVendor],
        outcome: SyncOutcome,
        external_by_id: Optional[Dict[str, QboVendorExternalSchema]] = None,
    ) -> None:
        """
        Sync vendors to Vendor module.

        U-513: the external record each staging row was built from is handed to
        the connector through a CLOSURE over `external_by_id`, rather than by
        widening `project_records`. `project_records` is shared by ten call
        sites across eight QBO families (seven besides this one); a closure
        keeps this family's extra argument entirely inside this family.

        U-513 ph2: retry + pacing live here too, for the same reason. This is
        now the ONLY vendor projection loop — `scripts/sync_qbo_vendor.py` (the
        path the scheduler and the admin dispatcher actually run) used to run
        its own copy with `with_retry` + `pace_batch` around it, and deleting
        that loop without these would have dropped transient-error retry and
        the inter-batch delay that keeps the DB connection alive under load.
        Pacing must tick once per record even when that record's projection
        RAISED, hence the `finally`; the per-record index it needs comes from a
        counter in this closure rather than from `project_records`, which stays
        untouched for its seven other families.

        Args:
            vendors: List of QboVendor staging records to project
            outcome: the pull's SyncOutcome (projection tier appends here)
            external_by_id: QBO id -> the external record that produced that
                staging row. Omitted/empty means every row projects with
                `external=None`, i.e. the pre-U-513 staging-read address path.
        """
        if not vendors:
            return

        # Import here to avoid circular dependencies
        from integrations.intuit.qbo.vendor.connector.vendor.business.service import VendorVendorConnector

        connector = VendorVendorConnector()
        by_id = external_by_id or {}

        total = len(vendors)
        counter = itertools.count()

        def _project(row: QboVendor):
            index = next(counter)
            try:
                return with_retry(
                    connector.sync_from_qbo_vendor,
                    row,
                    by_id.get(row.qbo_id),
                    max_retries=MAX_RETRIES,
                    initial_delay=INITIAL_RETRY_DELAY,
                )
            finally:
                # Add delay between batches to keep the connection alive
                pace_batch(index, total, logger, "vendors")

        project_records(
            vendors,
            outcome,
            label="QboVendor->Vendor",
            project_one=_project,
            logger=logger,
        )

    def read_all(self) -> List[QboVendor]:
        """
        Read all QboVendors.
        """
        return self.repo.read_all()

    def read_by_realm_id(self, realm_id: str) -> List[QboVendor]:
        """
        Read all QboVendors by realm ID.
        """
        return self.repo.read_by_realm_id(realm_id)

    def read_by_qbo_id(self, qbo_id: str) -> Optional[QboVendor]:
        """
        Read a QboVendor by QBO ID.
        """
        return self.repo.read_by_qbo_id(qbo_id)

    def read_by_id(self, id: int) -> Optional[QboVendor]:
        """
        Read a QboVendor by database ID.
        """
        return self.repo.read_by_id(id)
