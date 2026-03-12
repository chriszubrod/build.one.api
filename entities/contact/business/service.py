# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.contact.business.model import Contact
from entities.contact.persistence.repo import ContactRepository


class ContactService:
    """
    Service for Contact entity business operations.
    """

    def __init__(self, repo: Optional[ContactRepository] = None):
        """Initialize the ContactService."""
        self.repo = repo or ContactRepository()

    def create(
        self,
        *,
        tenant_id: int = None,
        email: str = None,
        office_phone: str = None,
        mobile_phone: str = None,
        fax: str = None,
        notes: str = None,
        user_id: int = None,
        company_id: int = None,
        customer_id: int = None,
        project_id: int = None,
        vendor_id: int = None,
    ) -> Contact:
        """
        Create a new contact.
        """
        return self.repo.create(
            email=email,
            office_phone=office_phone,
            mobile_phone=mobile_phone,
            fax=fax,
            notes=notes,
            user_id=user_id,
            company_id=company_id,
            customer_id=customer_id,
            project_id=project_id,
            vendor_id=vendor_id,
        )

    def read_all(self) -> list[Contact]:
        """
        Read all contacts.
        """
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[Contact]:
        """
        Read a contact by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[Contact]:
        """
        Read a contact by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_user_id(self, user_id: int) -> list[Contact]:
        """
        Read contacts by user ID.
        """
        return self.repo.read_by_user_id(user_id)

    def read_by_company_id(self, company_id: int) -> list[Contact]:
        """
        Read contacts by company ID.
        """
        return self.repo.read_by_company_id(company_id)

    def read_by_customer_id(self, customer_id: int) -> list[Contact]:
        """
        Read contacts by customer ID.
        """
        return self.repo.read_by_customer_id(customer_id)

    def read_by_project_id(self, project_id: int) -> list[Contact]:
        """
        Read contacts by project ID.
        """
        return self.repo.read_by_project_id(project_id)

    def read_by_vendor_id(self, vendor_id: int) -> list[Contact]:
        """
        Read contacts by vendor ID.
        """
        return self.repo.read_by_vendor_id(vendor_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        email: str = None,
        office_phone: str = None,
        mobile_phone: str = None,
        fax: str = None,
        notes: str = None,
    ) -> Optional[Contact]:
        """
        Update a contact by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if email is not None:
                existing.email = email
            if office_phone is not None:
                existing.office_phone = office_phone
            if mobile_phone is not None:
                existing.mobile_phone = mobile_phone
            if fax is not None:
                existing.fax = fax
            if notes is not None:
                existing.notes = notes
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[Contact]:
        """
        Delete a contact by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
