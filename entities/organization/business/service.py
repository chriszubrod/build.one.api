# Python Standard Library Imports

# Third-party Imports

# Local Imports
from entities.organization.persistence.repo import OrganizationRepository
from entities.organization.business.model import Organization
from shared.authz import current_user_id
from typing import Optional


class OrganizationService:
    """
    Service for Organization entity business operations.
    """

    def __init__(self, repo: Optional[OrganizationRepository] = None):
        """Initialize the OrganizationService."""
        self.repo = repo or OrganizationRepository()

    def create(
        self,
        *,
        tenant_id: int = None,
        name: str,
        website: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
    ) -> Organization:
        """
        Create a new organization. CreatedByUserId / ModifiedByUserId
        are pulled from the per-request ContextVar when not supplied.
        """
        actor = created_by_user_id if created_by_user_id is not None else current_user_id.get()
        return self.repo.create(
            name=name,
            website=website,
            created_by_user_id=actor,
            modified_by_user_id=actor,
        )

    def read_all(self) -> list[Organization]:
        """
        Read all organizations.
        """
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[Organization]:
        """
        Read an organization by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[Organization]:
        """
        Read an organization by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_name(self, name: str) -> Optional[Organization]:
        """
        Read an organization by name.
        """
        return self.repo.read_by_name(name)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        name: str = None,
        website: str = None,
        modified_by_user_id: Optional[int] = None,
    ) -> Optional[Organization]:
        """
        Update an organization by public ID. ModifiedByUserId is pulled
        from the per-request ContextVar when not supplied.
        """
        _org = self.read_by_public_id(public_id=public_id)
        if _org:
            _org.row_version = row_version
            if name is not None:
                _org.name = name
            if website is not None:
                _org.website = website
            _org.modified_by_user_id = (
                modified_by_user_id
                if modified_by_user_id is not None
                else current_user_id.get()
            )
        return self.repo.update_by_id(_org)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[Organization]:
        """
        Delete an organization by public ID.
        """
        _org = self.read_by_public_id(public_id=public_id)
        return self.repo.delete_by_id(_org.id)
