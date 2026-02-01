# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.customer.connector.customer.business.model import CustomerCustomer
from integrations.intuit.qbo.customer.connector.customer.persistence.repo import CustomerCustomerRepository
from integrations.intuit.qbo.customer.business.model import QboCustomer
from entities.customer.business.service import CustomerService
from entities.customer.business.model import Customer

logger = logging.getLogger(__name__)


class CustomerCustomerConnector:
    """
    Connector service for synchronization between QboCustomer and Customer modules.
    Handles parent QBO Customers (Job=false) mapping to Customer.
    """

    def __init__(
        self,
        mapping_repo: Optional[CustomerCustomerRepository] = None,
        customer_service: Optional[CustomerService] = None,
    ):
        """Initialize the CustomerCustomerConnector."""
        self.mapping_repo = mapping_repo or CustomerCustomerRepository()
        self.customer_service = customer_service or CustomerService()

    def sync_from_qbo_customer(self, qbo_customer: QboCustomer) -> Customer:
        """
        Sync data from QboCustomer to Customer module.
        
        This method:
        1. Checks if a mapping exists
        2. Creates or updates the Customer accordingly
        
        Args:
            qbo_customer: QboCustomer record (must be a parent customer with Job=false)
        
        Returns:
            Customer: The synced Customer record
        
        Raises:
            ValueError: If the customer has Job=true (is not a parent customer)
        """
        if qbo_customer.is_job:
            raise ValueError(f"QboCustomer {qbo_customer.id} has Job=true and is not a parent customer")
        
        # Map QBO Customer fields to Customer module fields
        customer_name = qbo_customer.display_name or qbo_customer.company_name or ""
        customer_email = qbo_customer.primary_email_addr or ""
        customer_phone = qbo_customer.primary_phone or qbo_customer.mobile or ""
        
        # Check for existing mapping
        mapping = self.mapping_repo.read_by_qbo_customer_id(qbo_customer.id)
        
        if mapping:
            # Found existing mapping - update the Customer
            customer = self.customer_service.read_by_id(mapping.customer_id)
            if customer:
                logger.info(f"Updating existing Customer {customer.id} from QboCustomer {qbo_customer.id}")
                customer.name = customer_name
                customer.email = customer_email
                customer.phone = customer_phone
                customer = self.customer_service.repo.update_by_id(customer)
                return customer
            else:
                # Mapping exists but Customer not found - recreate Customer
                logger.warning(f"Mapping exists but Customer {mapping.customer_id} not found. Creating new Customer.")
                self.mapping_repo.delete_by_id(mapping.id)
                mapping = None
        
        # Create new Customer
        logger.info(f"Creating new Customer from QboCustomer {qbo_customer.id}: name={customer_name}")
        customer = self.customer_service.create(
            name=customer_name,
            email=customer_email,
            phone=customer_phone
        )
        
        # Create mapping
        customer_id = int(customer.id) if isinstance(customer.id, str) else customer.id
        try:
            mapping = self.create_mapping(customer_id=customer_id, qbo_customer_id=qbo_customer.id)
            logger.info(f"Created mapping: Customer {customer_id} <-> QboCustomer {qbo_customer.id}")
        except ValueError as e:
            logger.warning(f"Could not create mapping: {e}")
        
        return customer

    def create_mapping(self, customer_id: int, qbo_customer_id: int) -> CustomerCustomer:
        """
        Create a mapping between Customer and QboCustomer.
        
        Args:
            customer_id: Database ID of Customer record
            qbo_customer_id: Database ID of QboCustomer record
        
        Returns:
            CustomerCustomer: The created mapping record
        
        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints
        existing_by_customer = self.mapping_repo.read_by_customer_id(customer_id)
        if existing_by_customer:
            raise ValueError(
                f"Customer {customer_id} is already mapped to QboCustomer {existing_by_customer.qbo_customer_id}"
            )
        
        existing_by_qbo_customer = self.mapping_repo.read_by_qbo_customer_id(qbo_customer_id)
        if existing_by_qbo_customer:
            raise ValueError(
                f"QboCustomer {qbo_customer_id} is already mapped to Customer {existing_by_qbo_customer.customer_id}"
            )
        
        # Create mapping
        return self.mapping_repo.create(customer_id=customer_id, qbo_customer_id=qbo_customer_id)

    def get_mapping_by_customer_id(self, customer_id: int) -> Optional[CustomerCustomer]:
        """
        Get mapping by Customer ID.
        """
        return self.mapping_repo.read_by_customer_id(customer_id)

    def get_mapping_by_qbo_customer_id(self, qbo_customer_id: int) -> Optional[CustomerCustomer]:
        """
        Get mapping by QboCustomer ID.
        """
        return self.mapping_repo.read_by_qbo_customer_id(qbo_customer_id)
