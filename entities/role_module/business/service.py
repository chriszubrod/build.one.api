# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.role_module.business.model import RoleModule
from entities.role_module.persistence.repo import RoleModuleRepository


class RoleModuleService:
    """
    Service for RoleModule entity business operations.
    """

    def __init__(self, repo: Optional[RoleModuleRepository] = None):
        """Initialize the RoleModuleService."""
        self.repo = repo or RoleModuleRepository()

    def create(
        self,
        *,
        tenant_id: int = None,
        role_id: int,
        module_id: int,
        can_create: bool = False,
        can_read: bool = False,
        can_update: bool = False,
        can_delete: bool = False,
        can_submit: bool = False,
        can_approve: bool = False,
        can_complete: bool = False,
    ) -> RoleModule:
        """
        Create a new role module.
        """
        # TODO: In Phase 10, use tenant_id for tenant isolation
        return self.repo.create(
            role_id=role_id,
            module_id=module_id,
            can_create=can_create,
            can_read=can_read,
            can_update=can_update,
            can_delete=can_delete,
            can_submit=can_submit,
            can_approve=can_approve,
            can_complete=can_complete,
        )

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

    def read_by_role_id(self, role_id: int) -> Optional[RoleModule]:
        """
        Read a role module by role ID.
        """
        return self.repo.read_by_role_id(role_id)

    def read_all_by_role_id(self, role_id: int) -> list[RoleModule]:
        """
        Read all role modules by role ID.
        """
        return self.repo.read_all_by_role_id(role_id)

    def read_by_module_id(self, module_id: int) -> Optional[RoleModule]:
        """
        Read a role module by module ID.
        """
        return self.repo.read_by_module_id(module_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        role_id: int = None,
        module_id: int = None,
        can_create: bool = None,
        can_read: bool = None,
        can_update: bool = None,
        can_delete: bool = None,
        can_submit: bool = None,
        can_approve: bool = None,
        can_complete: bool = None,
    ) -> Optional[RoleModule]:
        """
        Update a role module by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if role_id is not None:
                existing.role_id = role_id
            if module_id is not None:
                existing.module_id = module_id
            if can_create is not None:
                existing.can_create = can_create
            if can_read is not None:
                existing.can_read = can_read
            if can_update is not None:
                existing.can_update = can_update
            if can_delete is not None:
                existing.can_delete = can_delete
            if can_submit is not None:
                existing.can_submit = can_submit
            if can_approve is not None:
                existing.can_approve = can_approve
            if can_complete is not None:
                existing.can_complete = can_complete
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[RoleModule]:
        """
        Delete a role module by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
