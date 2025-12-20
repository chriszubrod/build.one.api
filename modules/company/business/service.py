# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from modules.company.business.model import Company
from modules.company.persistence.repo import CompanyRepository


class CompanyService:
    """
    Service for Company entity business operations.
    """

    def __init__(self, repo: Optional[CompanyRepository] = None):
        """Initialize the CompanyService."""
        self.repo = repo or CompanyRepository()

    def create(self, *, name: str, website: str) -> Company:
        """
        Create a new company.
        """
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

    def update_by_public_id(self, public_id: str, company) -> Optional[Company]:
        """
        Update a company by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = company.row_version
            existing.name = company.name
            existing.website = company.website
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str) -> Optional[Company]:
        """
        Delete a company by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
