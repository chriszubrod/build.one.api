# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.user_project.business.model import UserProject
from entities.user_project.persistence.repo import UserProjectRepository
from shared.authz import current_user_id


class UserProjectService:
    """
    Service for UserProject entity business operations.
    """

    def __init__(self, repo: Optional[UserProjectRepository] = None):
        """Initialize the UserProjectService."""
        self.repo = repo or UserProjectRepository()

    def create(
        self,
        *,
        tenant_id: int = None,
        user_id: int,
        project_id: int,
        created_by_user_id: Optional[int] = None,
    ) -> UserProject:
        actor = created_by_user_id if created_by_user_id is not None else current_user_id.get()
        return self.repo.create(
            user_id=user_id,
            project_id=project_id,
            created_by_user_id=actor,
            modified_by_user_id=actor,
        )

    def read_all(self) -> list[UserProject]:
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[UserProject]:
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[UserProject]:
        return self.repo.read_by_public_id(public_id)

    def read_by_user_id(self, user_id: int) -> list[UserProject]:
        return self.repo.read_by_user_id(user_id)

    def read_by_project_id(self, project_id: int) -> list[UserProject]:
        return self.repo.read_by_project_id(project_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        user_id: int = None,
        project_id: int = None,
        modified_by_user_id: Optional[int] = None,
    ) -> Optional[UserProject]:
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if user_id is not None:
                existing.user_id = user_id
            if project_id is not None:
                existing.project_id = project_id
            existing.modified_by_user_id = (
                modified_by_user_id
                if modified_by_user_id is not None
                else current_user_id.get()
            )
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[UserProject]:
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
