# Python Standard Library Imports

# Third-party Imports

# Local Imports
from modules.organization.persistence.repo import OrganizationRepository
from modules.organization.business.model import Organization
from typing import Optional


class OrganizationService:
    """
    Service for Organization entity business operations.
    """

    def __init__(self, repo: Optional[OrganizationRepository] = None):
        """Initialize the OrganizationService."""
        self.repo = repo or OrganizationRepository()

    def create(self, *, name: str, website: Optional[str] = None) -> Organization:
        """
        Create a new organization.
        """
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
    
    def update_by_public_id(self, public_id: str, org: Organization) -> Optional[Organization]:
        """
        Update an organization by ID.
        """
        _org = self.read_by_public_id(public_id=public_id)
        print("org service")
        print(_org)
        if _org:
            _org.row_version = org.row_version
            _org.name = org.name
            _org.website = org.website
        return self.repo.update_by_id(_org)
        
    
    def delete_by_public_id(self, public_id: str) -> Optional[Organization]:
        """
        Soft delete an organization by public ID.
        """
        _org = self.read_by_public_id(public_id=public_id)
        return self.repo.delete_by_id(_org.id)
