# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.user.business.model import User
from entities.user.persistence.repo import UserRepository
from shared.authz import current_user_id


class UserService:
    """
    Service for User entity business operations.
    """

    def __init__(self, repo: Optional[UserRepository] = None):
        """Initialize the UserService."""
        self.repo = repo or UserRepository()

    def create(
        self,
        *,
        tenant_id: int = None,
        firstname: str,
        lastname: str,
        created_by_user_id: Optional[int] = None,
    ) -> User:
        """
        Create a new user. CreatedByUserId / ModifiedByUserId are pulled
        from the per-request ContextVar when not supplied. Signup paths
        (no authenticated actor) leave them NULL.
        """
        actor = created_by_user_id if created_by_user_id is not None else current_user_id.get()
        return self.repo.create(
            firstname=firstname,
            lastname=lastname,
            created_by_user_id=actor,
            modified_by_user_id=actor,
        )

    def read_all(self, *, include_agents: bool = False) -> list[User]:
        """
        Read users. By default agent users (IsAgent=1) are hidden;
        pass include_agents=True for an admin Agents tab.
        """
        return self.repo.read_all(include_agents=include_agents)

    def read_by_id(self, id: str) -> Optional[User]:
        """
        Read a user by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[User]:
        """
        Read a user by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_firstname(self, firstname: str) -> Optional[User]:
        """
        Read a user by firstname.
        """
        return self.repo.read_by_firstname(firstname)

    def read_by_lastname(self, lastname: str) -> Optional[User]:
        """
        Read a user by lastname.
        """
        return self.repo.read_by_lastname(lastname)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        firstname: str = None,
        lastname: str = None,
        modified_by_user_id: Optional[int] = None,
    ) -> Optional[User]:
        """
        Update a user by public ID. ModifiedByUserId pulled from
        ContextVar when not supplied.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if firstname is not None:
                existing.firstname = firstname
            if lastname is not None:
                existing.lastname = lastname
            existing.modified_by_user_id = (
                modified_by_user_id
                if modified_by_user_id is not None
                else current_user_id.get()
            )
        return self.repo.update_by_id(existing)

    def set_last_company_id(self, *, user_id: int, last_company_id: int) -> None:
        """
        Persist the active Company a user last switched to so their next
        login defaults `cid` to it.
        """
        self.repo.set_last_company_id(user_id=user_id, last_company_id=last_company_id)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[User]:
        """
        Delete a user by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
