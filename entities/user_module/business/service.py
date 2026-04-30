# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.user_module.business.model import UserModule
from entities.user_module.persistence.repo import UserModuleRepository
from shared.authz import current_company_id, current_user_id


class UserModuleService:
    """
    Service for UserModule entity business operations.
    """

    def __init__(self, repo: Optional[UserModuleRepository] = None):
        """Initialize the UserModuleService."""
        self.repo = repo or UserModuleRepository()

    def create(
        self,
        *,
        tenant_id: int = None,
        user_id: int,
        module_id: int,
        company_id: Optional[int] = None,
        created_by_user_id: Optional[int] = None,
    ) -> UserModule:
        """
        Create a UserModule additive grant. CompanyId defaults to the
        active Company; CreatedByUserId / ModifiedByUserId default to
        the active subject.
        """
        cid = company_id if company_id is not None else current_company_id.get()
        actor = created_by_user_id if created_by_user_id is not None else current_user_id.get()
        return self.repo.create(
            user_id=user_id,
            module_id=module_id,
            company_id=cid,
            created_by_user_id=actor,
            modified_by_user_id=actor,
        )

    def read_all(self) -> list[UserModule]:
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[UserModule]:
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[UserModule]:
        return self.repo.read_by_public_id(public_id)

    def read_by_user_id(self, user_id: int) -> Optional[UserModule]:
        return self.repo.read_by_user_id(user_id)

    def read_all_by_user_id(self, user_id: int) -> list[UserModule]:
        return self.repo.read_all_by_user_id(user_id)

    def read_all_by_user_id_and_company_id(
        self, *, user_id: int, company_id: int
    ) -> list[UserModule]:
        """Phase 2 permission resolver entry point."""
        return self.repo.read_all_by_user_id_and_company_id(
            user_id=user_id, company_id=company_id
        )

    def read_by_module_id(self, module_id: int) -> Optional[UserModule]:
        return self.repo.read_by_module_id(module_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        user_id: int = None,
        module_id: int = None,
        company_id: Optional[int] = None,
        modified_by_user_id: Optional[int] = None,
    ) -> Optional[UserModule]:
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if user_id is not None:
                existing.user_id = user_id
            if module_id is not None:
                existing.module_id = module_id
            if company_id is not None:
                existing.company_id = company_id
            existing.modified_by_user_id = (
                modified_by_user_id
                if modified_by_user_id is not None
                else current_user_id.get()
            )
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[UserModule]:
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
