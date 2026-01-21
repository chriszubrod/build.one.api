# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from modules.payment_term.business.model import PaymentTerm
from modules.payment_term.persistence.repo import PaymentTermRepository


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
        name: Optional[str],
        description: Optional[str],
        discount_percent: Optional[float] = None,
        discount_days: Optional[int] = None,
        due_days: Optional[int] = None,
    ) -> PaymentTerm:
        """
        Create a new payment term.
        """
        return self.repo.create(
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

    def update_by_public_id(self, public_id: str, payment_term) -> Optional[PaymentTerm]:
        """
        Update a payment term by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = payment_term.row_version
            if hasattr(payment_term, 'name') and payment_term.name is not None:
                existing.name = payment_term.name
            if hasattr(payment_term, 'description') and payment_term.description is not None:
                existing.description = payment_term.description
            if hasattr(payment_term, 'discount_percent') and payment_term.discount_percent is not None:
                existing.discount_percent = payment_term.discount_percent
            if hasattr(payment_term, 'discount_days') and payment_term.discount_days is not None:
                existing.discount_days = payment_term.discount_days
            if hasattr(payment_term, 'due_days') and payment_term.due_days is not None:
                existing.due_days = payment_term.due_days
            return self.repo.update_by_id(existing)
        return None

    def delete_by_public_id(self, public_id: str) -> Optional[PaymentTerm]:
        """
        Delete a payment term by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
