# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.organization_company.business.model import OrganizationCompany
from entities.organization_company.persistence.repo import OrganizationCompanyRepository


class OrganizationCompanyService:
    """Service for OrganizationCompany entity business operations."""

    def __init__(self, repo: Optional[OrganizationCompanyRepository] = None):
        self.repo = repo or OrganizationCompanyRepository()

    def create(self, *, tenant_id: int = None, organization_id: int, company_id: int) -> OrganizationCompany:
        return self.repo.create(organization_id=organization_id, company_id=company_id)

    def read_all(self) -> list[OrganizationCompany]:
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[OrganizationCompany]:
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[OrganizationCompany]:
        return self.repo.read_by_public_id(public_id)

    def read_all_by_organization_id(self, organization_id: int) -> list[OrganizationCompany]:
        return self.repo.read_all_by_organization_id(organization_id=organization_id)

    def read_all_by_company_id(self, company_id: int) -> list[OrganizationCompany]:
        return self.repo.read_all_by_company_id(company_id=company_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        organization_id: int = None,
        company_id: int = None,
    ) -> Optional[OrganizationCompany]:
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if organization_id is not None:
                existing.organization_id = organization_id
            if company_id is not None:
                existing.company_id = company_id
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[OrganizationCompany]:
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
