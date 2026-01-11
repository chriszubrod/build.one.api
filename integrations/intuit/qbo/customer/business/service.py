# Python Standard Library Imports
import logging
from typing import List, Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.customer.business.model import QboCustomer
from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository
from integrations.intuit.qbo.customer.external.client import QboCustomerClient
from integrations.intuit.qbo.customer.external.schemas import QboCustomer as QboCustomerExternalSchema
from integrations.intuit.qbo.customer.connector.customer.business.service import CustomerCustomerConnector
from integrations.intuit.qbo.customer.connector.project.business.service import CustomerProjectConnector
from integrations.intuit.qbo.auth.business.service import QboAuthService
from integrations.intuit.qbo.physical_address.business.service import QboPhysicalAddressService

logger = logging.getLogger(__name__)


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
    ) -> List[QboCustomer]:
        """
        Fetch Customers from QBO API and store locally.
        Uses upsert pattern: creates if not exists, updates if exists.
        
        Args:
            realm_id: QBO company realm ID
            last_updated_time: Optional ISO format datetime string. If provided, only fetches
                Customers where Metadata.LastUpdatedTime > last_updated_time.
            sync_to_modules: If True, also sync to Customer/Project modules
        
        Returns:
            List[QboCustomer]: The synced customer records
        """
        # Get valid access token
        auth_service = QboAuthService()
        qbo_auth = auth_service.ensure_valid_token(realm_id=realm_id)
        
        if not qbo_auth or not qbo_auth.access_token:
            raise ValueError(f"No valid access token found for realm_id: {realm_id}")
        
        # Fetch Customers from QBO API
        with QboCustomerClient(
            access_token=qbo_auth.access_token,
            realm_id=realm_id
        ) as client:
            qbo_customers: List[QboCustomerExternalSchema] = client.query_all_customers(
                last_updated_time=last_updated_time
            )
        
        if not qbo_customers:
            logger.info(f"No Customers found since {last_updated_time or 'beginning'}")
            return []
        
        logger.info(f"Retrieved {len(qbo_customers)} customers from QBO")
        
        # Process each customer
        synced_customers = []
        parent_customers = []
        job_customers = []
        
        for qbo_customer in qbo_customers:
            # Store in local database
            local_customer = self._upsert_customer(qbo_customer, realm_id)
            synced_customers.append(local_customer)
            
            # Categorize for module sync
            if qbo_customer.job:
                job_customers.append(local_customer)
            else:
                parent_customers.append(local_customer)
        
        # Sync to modules if requested
        if sync_to_modules:
            self._sync_to_customers(parent_customers)
            self._sync_to_projects(job_customers)
        
        return synced_customers

    def _upsert_customer(self, qbo_customer: QboCustomerExternalSchema, realm_id: str) -> QboCustomer:
        """
        Create or update a QboCustomer record.
        
        Args:
            qbo_customer: QBO Customer from external API
            realm_id: QBO realm ID
        
        Returns:
            QboCustomer: The created or updated record
        """
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

    def _sync_to_customers(self, parent_customers: List[QboCustomer]) -> None:
        """
        Sync parent customers to Customer module.
        
        Args:
            parent_customers: List of parent QboCustomer records (Job=false)
        """
        if not parent_customers:
            return
        
        connector = CustomerCustomerConnector()
        
        for customer in parent_customers:
            try:
                customer_module = connector.sync_from_qbo_customer(customer)
                logger.info(f"Synced QboCustomer {customer.id} to Customer {customer_module.id}")
            except Exception as e:
                logger.error(f"Failed to sync QboCustomer {customer.id} to Customer: {e}")

    def _sync_to_projects(self, job_customers: List[QboCustomer]) -> None:
        """
        Sync job customers to Project module.
        
        Args:
            job_customers: List of job QboCustomer records (Job=true)
        """
        if not job_customers:
            return
        
        connector = CustomerProjectConnector()
        
        for customer in job_customers:
            try:
                project = connector.sync_from_qbo_customer(customer)
                logger.info(f"Synced QboCustomer {customer.id} to Project {project.id}")
            except Exception as e:
                logger.error(f"Failed to sync QboCustomer {customer.id} to Project: {e}")

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
