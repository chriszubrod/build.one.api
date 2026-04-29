# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.user_company.business.model import UserCompany
from entities.user_company.persistence.repo import UserCompanyRepository


class UserCompanyService:
    """Service for UserCompany entity business operations."""

    def __init__(self, repo: Optional[UserCompanyRepository] = None):
        self.repo = repo or UserCompanyRepository()

    def create(self, *, tenant_id: int = None, user_id: int, company_id: int) -> UserCompany:
        return self.repo.create(user_id=user_id, company_id=company_id)

    def read_all(self) -> list[UserCompany]:
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[UserCompany]:
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[UserCompany]:
        return self.repo.read_by_public_id(public_id)

    def read_by_user_id(self, user_id: int) -> Optional[UserCompany]:
        return self.repo.read_by_user_id(user_id)

    def read_all_by_user_id(self, user_id: int) -> list[UserCompany]:
        return self.repo.read_all_by_user_id(user_id=user_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        user_id: int = None,
        company_id: int = None,
    ) -> Optional[UserCompany]:
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if user_id is not None:
                existing.user_id = user_id
            if company_id is not None:
                existing.company_id = company_id
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[UserCompany]:
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
