# Python Standard Library Imports
import logging
import time
from typing import List, Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.vendor.business.model import QboVendor
from integrations.intuit.qbo.vendor.persistence.repo import QboVendorRepository
from integrations.intuit.qbo.vendor.external.client import QboVendorClient
from integrations.intuit.qbo.vendor.external.schemas import QboVendor as QboVendorExternalSchema
from integrations.intuit.qbo.physical_address.business.service import QboPhysicalAddressService
from shared.database import with_retry, is_transient_error

logger = logging.getLogger(__name__)

# Sync configuration
BATCH_SIZE = 10  # Process vendors in batches
BATCH_DELAY = 0.5  # Delay between batches (seconds)
MAX_RETRIES = 3  # Max retries for transient errors
INITIAL_RETRY_DELAY = 2.0  # Initial retry delay (seconds)


class QboVendorService:
    """
    Service for QboVendor entity business operations.
    """

    def __init__(self, repo: Optional[QboVendorRepository] = None):
        """Initialize the QboVendorService."""
        self.repo = repo or QboVendorRepository()
        self.physical_address_service = QboPhysicalAddressService()

    def sync_from_qbo(
        self,
        realm_id: str,
        last_updated_time: Optional[str] = None,
        sync_to_modules: bool = False
    ) -> List[QboVendor]:
        """
        Fetch Vendors from QBO API and store locally.
        Uses upsert pattern: creates if not exists, updates if exists.
        
        Args:
            realm_id: QBO company realm ID
            last_updated_time: Optional ISO format datetime string. If provided, only fetches
                Vendors where Metadata.LastUpdatedTime > last_updated_time.
            sync_to_modules: If True, also sync to Vendor/VendorAddress modules
        
        Returns:
            List[QboVendor]: The synced vendor records
        """
        # Fetch Vendors from QBO API. QboHttpClient (via QboVendorClient) resolves
        # and refreshes the access token lazily, so no upfront auth call is needed.
        with QboVendorClient(realm_id=realm_id) as client:
            qbo_vendors: List[QboVendorExternalSchema] = client.query_all_vendors(
                last_updated_time=last_updated_time
            )
        
        if not qbo_vendors:
            logger.info(f"No Vendors found since {last_updated_time or 'beginning'}")
            return []
        
        logger.info(f"Retrieved {len(qbo_vendors)} vendors from QBO")
        
        # Process each vendor with retry logic and batch delays
        synced_vendors = []
        failed_vendors = []
        
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
                synced_vendors.append(local_vendor)
                logger.debug(f"Upserted vendor {qbo_vendor.id} ({i + 1}/{len(qbo_vendors)})")
            except Exception as e:
                logger.error(f"Failed to upsert vendor {qbo_vendor.id}: {e}")
                failed_vendors.append(qbo_vendor.id)
            
            # Add delay between batches to prevent connection exhaustion
            if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(qbo_vendors):
                logger.debug(f"Processed {i + 1}/{len(qbo_vendors)} vendors, pausing...")
                time.sleep(BATCH_DELAY)
        
        if failed_vendors:
            logger.warning(f"Failed to upsert {len(failed_vendors)} vendors: {failed_vendors}")
        
        # Sync to modules if requested
        if sync_to_modules:
            self._sync_to_vendors(synced_vendors)
        
        return synced_vendors

    def _upsert_vendor(self, qbo_vendor: QboVendorExternalSchema, realm_id: str) -> QboVendor:
        """
        Create or update a QboVendor record.
        
        Args:
            qbo_vendor: QBO Vendor from external API
            realm_id: QBO realm ID
        
        Returns:
            QboVendor: The created or updated record
        """
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
        
        # Create/update bill address and get ID
        bill_addr_id = None
        if qbo_vendor.bill_addr:
            bill_addr_id = self._upsert_physical_address(
                qbo_address=qbo_vendor.bill_addr,
                qbo_id=f"{qbo_vendor.id}_bill",
                realm_id=realm_id
            )
        
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
                bill_addr_id=bill_addr_id,
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
                bill_addr_id=bill_addr_id,
                balance=qbo_vendor.balance,
                acct_num=qbo_vendor.acct_num,
                web_addr=web_addr,
            )

    def _sync_to_vendors(self, vendors: List[QboVendor]) -> None:
        """
        Sync vendors to Vendor module.
        
        Args:
            vendors: List of QboVendor records
        """
        if not vendors:
            return
        
        # Import here to avoid circular dependencies
        from integrations.intuit.qbo.vendor.connector.vendor.business.service import VendorVendorConnector
        
        connector = VendorVendorConnector()
        
        for vendor in vendors:
            try:
                vendor_module = connector.sync_from_qbo_vendor(vendor)
                logger.info(f"Synced QboVendor {vendor.id} to Vendor {vendor_module.id}")
            except Exception as e:
                logger.error(f"Failed to sync QboVendor {vendor.id} to Vendor: {e}")

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
                line1=qbo_address.line1,
                line2=qbo_address.line2,
                city=qbo_address.city,
                country=qbo_address.country,
                country_sub_division_code=qbo_address.country_sub_division_code,
                postal_code=qbo_address.postal_code,
            )
            return created.id if created else None

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
