# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from services.payment_term.business.model import PaymentTerm
from services.payment_term.persistence.repo import PaymentTermRepository


class PaymentTermService:
    """
    Service for PaymentTerm entity business operations.
    """

    def __init__(self, repo: Optional[PaymentTermRepository] = None):
        """Initialize the PaymentTermService."""
        self.repo = repo or PaymentTermRepository()

    def create(
        self,
        *,
        tenant_id: int = 1,
        name: Optional[str],
        description: Optional[str],
        discount_percent: Optional[float] = None,
        discount_days: Optional[int] = None,
        due_days: Optional[int] = None,
    ) -> PaymentTerm:
        """
        Create a new payment term.
        
        Args:
            tenant_id: Tenant ID for multi-tenant isolation (default: 1)
            name: Payment term name
            description: Payment term description
            discount_percent: Discount percentage (optional)
            discount_days: Discount days (optional)
            due_days: Due days (optional)
        """
        return self.repo.create(
            tenant_id=tenant_id,
            name=name,
            description=description,
            discount_percent=discount_percent,
            discount_days=discount_days,
            due_days=due_days,
        )

    def read_all(self) -> list[PaymentTerm]:
        """
        Read all payment terms.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[PaymentTerm]:
        """
        Read a payment term by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[PaymentTerm]:
        """
        Read a payment term by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_name(self, name: str) -> Optional[PaymentTerm]:
        """
        Read a payment term by name.
        """
        return self.repo.read_by_name(name)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        name: str = None,
        description: str = None,
        discount_percent: float = None,
        discount_days: int = None,
        due_days: int = None,
    ) -> Optional[PaymentTerm]:
        """
        Update a payment term by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if name is not None:
                existing.name = name
            if description is not None:
                existing.description = description
            if discount_percent is not None:
                existing.discount_percent = discount_percent
            if discount_days is not None:
                existing.discount_days = discount_days
            if due_days is not None:
                existing.due_days = due_days
            return self.repo.update_by_id(existing)
        return None

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[PaymentTerm]:
        """
        Delete a payment term by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
