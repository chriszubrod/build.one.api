# Python Standard Library Imports

# Third-party Imports

# Local Imports
from entities.organization.persistence.repo import OrganizationRepository
from entities.organization.business.model import Organization
from typing import Optional


class OrganizationService:
    """
    Service for Organization entity business operations.
    """

    def __init__(self, repo: Optional[OrganizationRepository] = None):
        """Initialize the OrganizationService."""
        self.repo = repo or OrganizationRepository()

    def create(self, *, tenant_id: int = None, name: str, website: Optional[str] = None) -> Organization:
        """
        Create a new organization.
        """
        # TODO: In Phase 10, use tenant_id for tenant isolation
        return self.repo.create(name=name, website=website)

    def read_all(self) -> list[Organization]:
        """
        Read all organizations.
        """
        return self.repo.read_all()
    
    def read_by_id(self, id: str) -> Optional[Organization]:
        """
        Read an organization by ID.
        """
        return self.repo.read_by_id(id)
    
    def read_by_public_id(self, public_id: str) -> Optional[Organization]:
        """
        Read an organization by public ID.
        """
        return self.repo.read_by_public_id(public_id)
    
    def read_by_name(self, name: str) -> Optional[Organization]:
        """
        Read an organization by name.
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
    ) -> Optional[Organization]:
        """
        Update an organization by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        _org = self.read_by_public_id(public_id=public_id)
        if _org:
            _org.row_version = row_version
            if name is not None:
                _org.name = name
            if website is not None:
                _org.website = website
        return self.repo.update_by_id(_org)
        
    
    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[Organization]:
        """
        Soft delete an organization by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        _org = self.read_by_public_id(public_id=public_id)
        return self.repo.delete_by_id(_org.id)
