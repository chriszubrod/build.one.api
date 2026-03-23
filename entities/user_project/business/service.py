# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.user_project.business.model import UserProject
from entities.user_project.persistence.repo import UserProjectRepository


class UserProjectService:
    """
    Service for UserProject entity business operations.
    """

    def __init__(self, repo: Optional[UserProjectRepository] = None):
        """Initialize the UserProjectService."""
        self.repo = repo or UserProjectRepository()

    def create(self, *, tenant_id: int = None, user_id: int, project_id: int) -> UserProject:
        """
        Create a new user project.
        """
        # TODO: In Phase 10, use tenant_id for tenant isolation
        return self.repo.create(user_id=user_id, project_id=project_id)

    def read_all(self) -> list[UserProject]:
        """
        Read all user projects.
        """
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[UserProject]:
        """
        Read a user project by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[UserProject]:
        """
        Read a user project by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_user_id(self, user_id: int) -> list[UserProject]:
        """
        Read user projects by user ID.
        """
        return self.repo.read_by_user_id(user_id)

    def read_by_project_id(self, project_id: int) -> list[UserProject]:
        """
        Read user projects by project ID.
        """
        return self.repo.read_by_project_id(project_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        user_id: int = None,
        project_id: int = None,
    ) -> Optional[UserProject]:
        """
        Update a user project by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if user_id is not None:
                existing.user_id = user_id
            if project_id is not None:
                existing.project_id = project_id
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[UserProject]:
        """
        Delete a user project by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
