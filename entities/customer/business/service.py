# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.customer.business.model import Customer
from entities.customer.persistence.repo import CustomerRepository


class CustomerService:
    """
    Service for Customer entity business operations.
    """

    def __init__(self, repo: Optional[CustomerRepository] = None):
        """Initialize the CustomerService."""
        self.repo = repo or CustomerRepository()

    def create(self, *, tenant_id: int = 1, name: str, email: str, phone: str) -> Customer:
        """
        Create a new customer.
        
        Args:
            tenant_id: Tenant ID for multi-tenant isolation (default: 1)
            name: Customer name
            email: Customer email
            phone: Customer phone
        """
        return self.repo.create(tenant_id=tenant_id, name=name, email=email, phone=phone)

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

    def search_by_name(self, *, query: str, limit: int = 10):
        """
        Case-insensitive substring search against Name (with email + phone
        as secondary fields). Prefix matches rank above substring matches.

        In-memory filter over `read_all()` — Customer is small (~70 rows)
        so this beats a dedicated LIKE sproc. Upgrade to a sproc if the
        table grows or fuzzy matching gets more complex.
        """
        q = (query or "").strip().lower()
        if not q or limit <= 0:
            return []

        prefix_hits = []
        substring_hits = []

        for customer in self.repo.read_all():
            name = (customer.name or "").lower()
            email = (customer.email or "").lower()
            phone = (customer.phone or "").lower()

            if name.startswith(q):
                prefix_hits.append(customer)
            elif q in name or q in email or q in phone:
                substring_hits.append(customer)

            if len(prefix_hits) >= limit:
                break

        return (prefix_hits + substring_hits)[:limit]

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        name: str = None,
        email: str = None,
        phone: str = None,
    ) -> Optional[Customer]:
        """
        Update a customer by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if name is not None:
                existing.name = name
            if email is not None:
                existing.email = email
            if phone is not None:
                existing.phone = phone
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[Customer]:
        """
        Delete a customer by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
