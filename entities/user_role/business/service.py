# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.user_role.business.model import UserRole
from entities.user_role.persistence.repo import UserRoleRepository
from shared.authz import current_company_id, current_user_id


class UserRoleService:
    """
    Service for UserRole entity business operations.
    """

    def __init__(self, repo: Optional[UserRoleRepository] = None):
        """Initialize the UserRoleService."""
        self.repo = repo or UserRoleRepository()

    def create(
        self,
        *,
        tenant_id: int = None,
        user_id: int,
        role_id: int,
        company_id: Optional[int] = None,
        created_by_user_id: Optional[int] = None,
    ) -> UserRole:
        """
        Create a UserRole row. CompanyId defaults to the active Company
        from the per-request ContextVar; CreatedByUserId / ModifiedByUserId
        default to the active subject. Admin paths can override either.
        """
        cid = company_id if company_id is not None else current_company_id.get()
        actor = created_by_user_id if created_by_user_id is not None else current_user_id.get()
        return self.repo.create(
            user_id=user_id,
            role_id=role_id,
            company_id=cid,
            created_by_user_id=actor,
            modified_by_user_id=actor,
        )

    def read_all(self) -> list[UserRole]:
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[UserRole]:
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[UserRole]:
        return self.repo.read_by_public_id(public_id)

    def read_by_user_id(self, user_id: int) -> Optional[UserRole]:
        return self.repo.read_by_user_id(user_id)

    def read_all_by_user_id(self, user_id: int) -> list[UserRole]:
        return self.repo.read_all_by_user_id(user_id=user_id)

    def read_all_by_user_id_and_company_id(
        self, *, user_id: int, company_id: int
    ) -> list[UserRole]:
        """Phase 2 permission resolver entry point."""
        return self.repo.read_all_by_user_id_and_company_id(
            user_id=user_id, company_id=company_id
        )

    def read_by_role_id(self, role_id: int) -> Optional[UserRole]:
        return self.repo.read_by_role_id(role_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        user_id: int = None,
        role_id: int = None,
        company_id: Optional[int] = None,
        modified_by_user_id: Optional[int] = None,
    ) -> Optional[UserRole]:
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if user_id is not None:
                existing.user_id = user_id
            if role_id is not None:
                existing.role_id = role_id
            if company_id is not None:
                existing.company_id = company_id
            existing.modified_by_user_id = (
                modified_by_user_id
                if modified_by_user_id is not None
                else current_user_id.get()
            )
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[UserRole]:
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
