# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.role.business.model import Role
from entities.role.persistence.repo import RoleRepository


class RoleService:
    """
    Service for Role entity business operations.
    """

    def __init__(self, repo: Optional[RoleRepository] = None):
        """Initialize the RoleService."""
        self.repo = repo or RoleRepository()

    def create(self, *, tenant_id: int = None, name: str) -> Role:
        """
        Create a new role.
        """
        # TODO: In Phase 10, use tenant_id for tenant isolation
        return self.repo.create(name=name)

    def read_all(self) -> list[Role]:
        """
        Read all roles.
        """
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[Role]:
        """
        Read a role by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[Role]:
        """
        Read a role by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_name(self, name: str) -> Optional[Role]:
        """
        Read a role by name.
        """
        return self.repo.read_by_name(name)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        name: str = None,
    ) -> Optional[Role]:
        """
        Update a role by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if name is not None:
                existing.name = name
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[Role]:
        """
        Delete a role by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
