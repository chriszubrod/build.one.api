# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.project_address.business.model import ProjectAddress
from entities.project_address.persistence.repo import ProjectAddressRepository


class ProjectAddressService:
    """
    Service for ProjectAddress entity business operations.
    """

    def __init__(self, repo: Optional[ProjectAddressRepository] = None):
        """Initialize the ProjectAddressService."""
        self.repo = repo or ProjectAddressRepository()

    def create(self, *, tenant_id: int = None, project_id: int, address_id: int, address_type_id: int) -> ProjectAddress:
        """
        Create a new project address.
        """
        # TODO: In Phase 10, use tenant_id for tenant isolation
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

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        project_id: int = None,
        address_id: int = None,
        address_type_id: int = None,
    ) -> Optional[ProjectAddress]:
        """
        Update a project address by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if project_id is not None:
                existing.project_id = project_id
            if address_id is not None:
                existing.address_id = address_id
            if address_type_id is not None:
                existing.address_type_id = address_type_id
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[ProjectAddress]:
        """
        Delete a project address by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
