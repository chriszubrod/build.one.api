# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from modules.customer.business.model import Customer
from modules.customer.persistence.repo import CustomerRepository


class CustomerService:
    """
    Service for Customer entity business operations.
    """

    def __init__(self, repo: Optional[CustomerRepository] = None):
        """Initialize the CustomerService."""
        self.repo = repo or CustomerRepository()

    def create(self, *, name: str, email: str, phone: str) -> Customer:
        """
        Create a new customer.
        """
        return self.repo.create(name=name, email=email, phone=phone)

    def read_all(self) -> list[Customer]:
        """
        Read all customers.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[Customer]:
        """
        Read a customer by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[Customer]:
        """
        Read a customer by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_name(self, name: str) -> Optional[Customer]:
        """
        Read a customer by name.
        """
        return self.repo.read_by_name(name)

    def update_by_public_id(self, public_id: str, customer) -> Optional[Customer]:
        """
        Update a customer by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = customer.row_version
            existing.name = customer.name
            existing.email = customer.email
            existing.phone = customer.phone
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str) -> Optional[Customer]:
        """
        Delete a customer by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
