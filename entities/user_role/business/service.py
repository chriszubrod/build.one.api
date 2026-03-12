# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.user_role.business.model import UserRole
from entities.user_role.persistence.repo import UserRoleRepository


class UserRoleService:
    """
    Service for UserRole entity business operations.
    """

    def __init__(self, repo: Optional[UserRoleRepository] = None):
        """Initialize the UserRoleService."""
        self.repo = repo or UserRoleRepository()

    def create(self, *, tenant_id: int = None, user_id: int, role_id: int) -> UserRole:
        """
        Create a new user role.
        """
        # TODO: In Phase 10, use tenant_id for tenant isolation
        return self.repo.create(user_id=user_id, role_id=role_id)

    def read_all(self) -> list[UserRole]:
        """
        Read all user roles.
        """
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[UserRole]:
        """
        Read a user role by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[UserRole]:
        """
        Read a user role by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_user_id(self, user_id: int) -> Optional[UserRole]:
        """
        Read a user role by user ID.
        """
        return self.repo.read_by_user_id(user_id)

    def read_by_role_id(self, role_id: int) -> Optional[UserRole]:
        """
        Read a user role by role ID.
        """
        return self.repo.read_by_role_id(role_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        user_id: int = None,
        role_id: int = None,
    ) -> Optional[UserRole]:
        """
        Update a user role by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if user_id is not None:
                existing.user_id = user_id
            if role_id is not None:
                existing.role_id = role_id
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[UserRole]:
        """
        Delete a user role by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
