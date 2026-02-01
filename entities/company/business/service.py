# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.company.business.model import Company
from entities.company.persistence.repo import CompanyRepository


class CompanyService:
    """
    Service for Company entity business operations.
    """

    def __init__(self, repo: Optional[CompanyRepository] = None):
        """Initialize the CompanyService."""
        self.repo = repo or CompanyRepository()

    def create(self, *, tenant_id: int = None, name: str, website: str) -> Company:
        """
        Create a new company.
        """
        # TODO: In Phase 10, use tenant_id for tenant isolation
        return self.repo.create(name=name, website=website)

    def read_all(self) -> list[Company]:
        """
        Read all companies.
        """
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[Company]:
        """
        Read a company by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[Company]:
        """
        Read a company by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_name(self, name: str) -> Optional[Company]:
        """
        Read a company by name.
        """
        return self.repo.read_by_name(name)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        name: str = None,
        website: str = None,
    ) -> Optional[Company]:
        """
        Update a company by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if name is not None:
                existing.name = name
            if website is not None:
                existing.website = website
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[Company]:
        """
        Delete a company by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
