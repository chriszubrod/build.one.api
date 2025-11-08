# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from modules.user_role.business.model import UserRole
from modules.user_role.persistence.repo import UserRoleRepository


class UserRoleService:
    """
    Service for UserRole entity business operations.
    """

    def __init__(self, repo: Optional[UserRoleRepository] = None):
        """Initialize the UserRoleService."""
        self.repo = repo or UserRoleRepository()

    def create(self, *, user_id: str, role_id: str) -> UserRole:
        """
        Create a new user.
        """
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

    def read_by_user_id(self, user_id: str) -> Optional[UserRole]:
        """
        Read a user role by user ID.
        """
        return self.repo.read_by_user_id(user_id)

    def read_by_role_id(self, role_id: str) -> Optional[UserRole]:
        """
        Read a user role by role ID.
        """
        return self.repo.read_by_role_id(role_id)

    def update_by_public_id(self, public_id: str, user_role) -> Optional[UserRole]:
        """
        Update a user role by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = user_role.row_version
            existing.user_id = user_role.user_id
            existing.role_id = user_role.role_id
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str) -> Optional[UserRole]:
        """
        Delete a user role by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
