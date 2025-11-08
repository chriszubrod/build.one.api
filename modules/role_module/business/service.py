# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from modules.role_module.business.model import RoleModule
from modules.role_module.persistence.repo import RoleModuleRepository


class RoleModuleService:
    """
    Service for RoleModule entity business operations.
    """

    def __init__(self, repo: Optional[RoleModuleRepository] = None):
        """Initialize the RoleModuleService."""
        self.repo = repo or RoleModuleRepository()

    def create(self, *, role_id: str, module_id: str) -> RoleModule:
        """
        Create a new role module.
        """
        return self.repo.create(role_id=role_id, module_id=module_id)

    def read_all(self) -> list[RoleModule]:
        """
        Read all role modules.
        """
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[RoleModule]:
        """
        Read a role module by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[RoleModule]:
        """
        Read a role module by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_role_id(self, role_id: str) -> Optional[RoleModule]:
        """
        Read a role module by role ID.
        """
        return self.repo.read_by_role_id(role_id)

    def read_by_module_id(self, module_id: str) -> Optional[RoleModule]:
        """
        Read a role module by module ID.
        """
        return self.repo.read_by_module_id(module_id)

    def update_by_public_id(self, public_id: str, role_module) -> Optional[RoleModule]:
        """
        Update a role module by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = role_module.row_version
            existing.role_id = role_module.role_id
            existing.module_id = role_module.module_id
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str) -> Optional[RoleModule]:
        """
        Delete a role module by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
