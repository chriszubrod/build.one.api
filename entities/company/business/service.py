# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.company.business.model import Company
from entities.company.persistence.repo import CompanyRepository
from shared.authz import current_user_id


class CompanyService:
    """
    Service for Company entity business operations.
    """

    def __init__(self, repo: Optional[CompanyRepository] = None):
        """Initialize the CompanyService."""
        self.repo = repo or CompanyRepository()

    def create(
        self,
        *,
        tenant_id: int = None,
        name: str,
        website: str,
        organization_id: Optional[int] = None,
        created_by_user_id: Optional[int] = None,
    ) -> Company:
        """
        Create a new company. Stamps actor + parent Organization.
        """
        actor = created_by_user_id if created_by_user_id is not None else current_user_id.get()
        return self.repo.create(
            name=name,
            website=website,
            organization_id=organization_id,
            created_by_user_id=actor,
            modified_by_user_id=actor,
        )

    def read_all(self) -> list[Company]:
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[Company]:
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[Company]:
        return self.repo.read_by_public_id(public_id)

    def read_by_name(self, name: str) -> Optional[Company]:
        return self.repo.read_by_name(name)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        name: str = None,
        website: str = None,
        organization_id: Optional[int] = None,
        modified_by_user_id: Optional[int] = None,
    ) -> Optional[Company]:
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if name is not None:
                existing.name = name
            if website is not None:
                existing.website = website
            if organization_id is not None:
                existing.organization_id = organization_id
            existing.modified_by_user_id = (
                modified_by_user_id
                if modified_by_user_id is not None
                else current_user_id.get()
            )
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[Company]:
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
