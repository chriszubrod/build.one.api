# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from modules.project_address.business.model import ProjectAddress
from modules.project_address.persistence.repo import ProjectAddressRepository


class ProjectAddressService:
    """
    Service for ProjectAddress entity business operations.
    """

    def __init__(self, repo: Optional[ProjectAddressRepository] = None):
        """Initialize the ProjectAddressService."""
        self.repo = repo or ProjectAddressRepository()

    def create(self, *, project_id: int, address_id: int, address_type_id: int) -> ProjectAddress:
        """
        Create a new project address.
        """
        return self.repo.create(project_id=project_id, address_id=address_id, address_type_id=address_type_id)

    def read_all(self) -> list[ProjectAddress]:
        """
        Read all project addresses.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[ProjectAddress]:
        """
        Read a project address by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[ProjectAddress]:
        """
        Read a project address by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_project_id(self, project_id: int) -> list[ProjectAddress]:
        """
        Read project addresses by project ID.
        """
        return self.repo.read_by_project_id(project_id=project_id)

    def read_by_address_id(self, address_id: int) -> list[ProjectAddress]:
        """
        Read project addresses by address ID.
        """
        return self.repo.read_by_address_id(address_id=address_id)

    def read_by_address_type_id(self, address_type_id: int) -> list[ProjectAddress]:
        """
        Read project addresses by address type ID.
        """
        return self.repo.read_by_address_type_id(address_type_id=address_type_id)

    def update_by_public_id(self, public_id: str, project_address: ProjectAddress) -> Optional[ProjectAddress]:
        """
        Update a project address by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = project_address.row_version
            existing.project_id = project_address.project_id
            existing.address_id = project_address.address_id
            existing.address_type_id = project_address.address_type_id
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str) -> Optional[ProjectAddress]:
        """
        Delete a project address by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
