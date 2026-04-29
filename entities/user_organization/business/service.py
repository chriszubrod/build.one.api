# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.user_organization.business.model import UserOrganization
from entities.user_organization.persistence.repo import UserOrganizationRepository


class UserOrganizationService:
    """Service for UserOrganization entity business operations."""

    def __init__(self, repo: Optional[UserOrganizationRepository] = None):
        self.repo = repo or UserOrganizationRepository()

    def create(self, *, tenant_id: int = None, user_id: int, organization_id: int) -> UserOrganization:
        return self.repo.create(user_id=user_id, organization_id=organization_id)

    def read_all(self) -> list[UserOrganization]:
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[UserOrganization]:
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[UserOrganization]:
        return self.repo.read_by_public_id(public_id)

    def read_by_user_id(self, user_id: int) -> Optional[UserOrganization]:
        return self.repo.read_by_user_id(user_id)

    def read_all_by_user_id(self, user_id: int) -> list[UserOrganization]:
        return self.repo.read_all_by_user_id(user_id=user_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        user_id: int = None,
        organization_id: int = None,
    ) -> Optional[UserOrganization]:
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if user_id is not None:
                existing.user_id = user_id
            if organization_id is not None:
                existing.organization_id = organization_id
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[UserOrganization]:
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
