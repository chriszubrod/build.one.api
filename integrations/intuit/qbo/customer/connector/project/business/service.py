# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.customer.connector.project.business.model import CustomerProject
from integrations.intuit.qbo.customer.connector.project.persistence.repo import CustomerProjectRepository
from integrations.intuit.qbo.customer.connector.customer.persistence.repo import CustomerCustomerRepository
from integrations.intuit.qbo.customer.business.model import QboCustomer
from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository
from integrations.intuit.qbo.physical_address.connector.business.service import PhysicalAddressAddressConnector
from modules.project.business.service import ProjectService
from modules.project.business.model import Project
from modules.project_address.business.service import ProjectAddressService

logger = logging.getLogger(__name__)

# Address type IDs (these would typically come from a lookup table)
ADDRESS_TYPE_BILLING = 1
ADDRESS_TYPE_SHIPPING = 2


class CustomerProjectConnector:
    """
    Connector service for synchronization between QboCustomer and Project modules.
    Handles job/sub-customer QBO Customers (Job=true) mapping to Project.
    
    Also syncs addresses from QboPhysicalAddress to Address via ProjectAddress.
    """

    def __init__(
        self,
        mapping_repo: Optional[CustomerProjectRepository] = None,
        project_service: Optional[ProjectService] = None,
        project_address_service: Optional[ProjectAddressService] = None,
        address_connector: Optional[PhysicalAddressAddressConnector] = None,
        customer_mapping_repo: Optional[CustomerCustomerRepository] = None,
    ):
        """Initialize the CustomerProjectConnector."""
        self.mapping_repo = mapping_repo or CustomerProjectRepository()
        self.project_service = project_service or ProjectService()
        self.project_address_service = project_address_service or ProjectAddressService()
        self.address_connector = address_connector or PhysicalAddressAddressConnector()
        self.customer_mapping_repo = customer_mapping_repo or CustomerCustomerRepository()

    def sync_from_qbo_customer(self, qbo_customer: QboCustomer) -> Project:
        """
        Sync data from QboCustomer to Project module.
        
        This method:
        1. Checks if a mapping exists
        2. Creates or updates the Project accordingly
        3. Syncs addresses (BillAddr, ShipAddr) to Address via ProjectAddress
        
        Args:
            qbo_customer: QboCustomer record (must be a job/sub-customer with Job=true)
        
        Returns:
            Project: The synced Project record
        
        Raises:
            ValueError: If the customer has Job=false (is not a job/sub-customer)
        """
        if not qbo_customer.is_job:
            raise ValueError(f"QboCustomer {qbo_customer.id} has Job=false and is not a job/sub-customer")
        
        # Map QBO Customer fields to Project module fields
        project_name = qbo_customer.display_name or qbo_customer.company_name or ""
        project_description = qbo_customer.notes or ""
        project_status = "active" if qbo_customer.active else "inactive"
        
        # Find the parent Customer ID if this job has a parent
        customer_id = None
        if qbo_customer.parent_ref_value:
            # Look up the parent QboCustomer and its mapping to Customer
            qbo_customer_repo = QboCustomerRepository()
            parent_qbo_customer = qbo_customer_repo.read_by_qbo_id(qbo_customer.parent_ref_value)
            if parent_qbo_customer:
                parent_mapping = self.customer_mapping_repo.read_by_qbo_customer_id(parent_qbo_customer.id)
                if parent_mapping:
                    customer_id = parent_mapping.customer_id
                    logger.debug(f"Found parent Customer {customer_id} for Project")
        
        # Check for existing mapping
        mapping = self.mapping_repo.read_by_qbo_customer_id(qbo_customer.id)
        
        if mapping:
            # Found existing mapping - update the Project
            project = self.project_service.read_by_id(mapping.project_id)
            if project:
                logger.info(f"Updating existing Project {project.id} from QboCustomer {qbo_customer.id}")
                project.name = project_name
                project.description = project_description
                project.status = project_status
                project.customer_id = customer_id
                project = self.project_service.repo.update_by_id(project)
                
                # Sync addresses for existing project
                self._sync_addresses(qbo_customer, project.id)
                
                return project
            else:
                # Mapping exists but Project not found - recreate Project
                logger.warning(f"Mapping exists but Project {mapping.project_id} not found. Creating new Project.")
                self.mapping_repo.delete_by_id(mapping.id)
                mapping = None
        
        # Create new Project
        logger.info(f"Creating new Project from QboCustomer {qbo_customer.id}: name={project_name}")
        project = self.project_service.create(
            name=project_name,
            description=project_description,
            status=project_status,
            customer_id=customer_id
        )
        
        # Create mapping
        project_id = int(project.id) if isinstance(project.id, str) else project.id
        try:
            mapping = self.create_mapping(project_id=project_id, qbo_customer_id=qbo_customer.id)
            logger.info(f"Created mapping: Project {project_id} <-> QboCustomer {qbo_customer.id}")
        except ValueError as e:
            logger.warning(f"Could not create mapping: {e}")
        
        # Sync addresses for new project
        self._sync_addresses(qbo_customer, project_id)
        
        return project

    def _sync_addresses(self, qbo_customer: QboCustomer, project_id: int) -> None:
        """
        Sync billing and shipping addresses from QboCustomer to ProjectAddress/Address.
        
        Args:
            qbo_customer: QboCustomer with bill_addr_id and ship_addr_id
            project_id: Database ID of the Project
        """
        # Sync billing address
        if qbo_customer.bill_addr_id:
            try:
                address = self.address_connector.sync_from_qbo_to_address(qbo_customer.bill_addr_id)
                address_id = int(address.id) if isinstance(address.id, str) else address.id
                self._ensure_project_address(project_id, address_id, ADDRESS_TYPE_BILLING)
                logger.debug(f"Synced billing address {address_id} for Project {project_id}")
            except Exception as e:
                logger.error(f"Failed to sync billing address for Project {project_id}: {e}")
        
        # Sync shipping address
        if qbo_customer.ship_addr_id:
            try:
                address = self.address_connector.sync_from_qbo_to_address(qbo_customer.ship_addr_id)
                address_id = int(address.id) if isinstance(address.id, str) else address.id
                self._ensure_project_address(project_id, address_id, ADDRESS_TYPE_SHIPPING)
                logger.debug(f"Synced shipping address {address_id} for Project {project_id}")
            except Exception as e:
                logger.error(f"Failed to sync shipping address for Project {project_id}: {e}")

    def _ensure_project_address(self, project_id: int, address_id: int, address_type_id: int) -> None:
        """
        Ensure a ProjectAddress record exists linking Project to Address.
        Creates if not exists, updates if exists with different address.
        
        Args:
            project_id: Database ID of the Project
            address_id: Database ID of the Address
            address_type_id: Type of address (billing/shipping)
        """
        # Check for existing ProjectAddress by project_id and address_type
        existing_addresses = self.project_address_service.read_by_project_id(project_id)
        existing = None
        for pa in existing_addresses:
            if pa.address_type_id == address_type_id:
                existing = pa
                break
        
        if existing:
            if existing.address_id != address_id:
                # Update with new address
                existing.address_id = address_id
                self.project_address_service.repo.update_by_id(existing)
                logger.debug(f"Updated ProjectAddress {existing.id} with new address {address_id}")
        else:
            # Create new ProjectAddress
            self.project_address_service.create(
                project_id=project_id,
                address_id=address_id,
                address_type_id=address_type_id
            )
            logger.debug(f"Created ProjectAddress for Project {project_id}, Address {address_id}, Type {address_type_id}")

    def create_mapping(self, project_id: int, qbo_customer_id: int) -> CustomerProject:
        """
        Create a mapping between Project and QboCustomer.
        
        Args:
            project_id: Database ID of Project record
            qbo_customer_id: Database ID of QboCustomer record
        
        Returns:
            CustomerProject: The created mapping record
        
        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints
        existing_by_project = self.mapping_repo.read_by_project_id(project_id)
        if existing_by_project:
            raise ValueError(
                f"Project {project_id} is already mapped to QboCustomer {existing_by_project.qbo_customer_id}"
            )
        
        existing_by_qbo_customer = self.mapping_repo.read_by_qbo_customer_id(qbo_customer_id)
        if existing_by_qbo_customer:
            raise ValueError(
                f"QboCustomer {qbo_customer_id} is already mapped to Project {existing_by_qbo_customer.project_id}"
            )
        
        # Create mapping
        return self.mapping_repo.create(project_id=project_id, qbo_customer_id=qbo_customer_id)

    def get_mapping_by_project_id(self, project_id: int) -> Optional[CustomerProject]:
        """
        Get mapping by Project ID.
        """
        return self.mapping_repo.read_by_project_id(project_id)

    def get_mapping_by_qbo_customer_id(self, qbo_customer_id: int) -> Optional[CustomerProject]:
        """
        Get mapping by QboCustomer ID.
        """
        return self.mapping_repo.read_by_qbo_customer_id(qbo_customer_id)
