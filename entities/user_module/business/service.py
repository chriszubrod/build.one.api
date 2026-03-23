# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.user_module.business.model import UserModule
from entities.user_module.persistence.repo import UserModuleRepository


class UserModuleService:
    """
    Service for UserModule entity business operations.
    """

    def __init__(self, repo: Optional[UserModuleRepository] = None):
        """Initialize the UserModuleService."""
        self.repo = repo or UserModuleRepository()

    def create(self, *, tenant_id: int = None, user_id: int, module_id: int) -> UserModule:
        """
        Create a new user module.
        """
        # TODO: In Phase 10, use tenant_id for tenant isolation
        return self.repo.create(user_id=user_id, module_id=module_id)

    def read_all(self) -> list[UserModule]:
        """
        Read all user modules.
        """
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[UserModule]:
        """
        Read a user module by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[UserModule]:
        """
        Read a user module by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_user_id(self, user_id: int) -> Optional[UserModule]:
        """
        Read a user module by user ID.
        """
        return self.repo.read_by_user_id(user_id)

    def read_all_by_user_id(self, user_id: int) -> list[UserModule]:
        """
        Read all user modules by user ID.
        """
        return self.repo.read_all_by_user_id(user_id)

    def read_by_module_id(self, module_id: int) -> Optional[UserModule]:
        """
        Read a user module by module ID.
        """
        return self.repo.read_by_module_id(module_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        user_id: int = None,
        module_id: int = None,
    ) -> Optional[UserModule]:
        """
        Update a user module by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if user_id is not None:
                existing.user_id = user_id
            if module_id is not None:
                existing.module_id = module_id
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[UserModule]:
        """
        Delete a user module by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
