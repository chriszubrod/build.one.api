# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from modules.user.business.model import User
from modules.user.persistence.repo import UserRepository


class UserService:
    """
    Service for User entity business operations.
    """

    def __init__(self, repo: Optional[UserRepository] = None):
        """Initialize the UserService."""
        self.repo = repo or UserRepository()

    def create(self, *, firstname: str, lastname: str) -> User:
        """
        Create a new user.
        """
        return self.repo.create(firstname=firstname, lastname=lastname)

    def read_all(self) -> list[User]:
        """
        Read all users.
        """
        return self.repo.read_all()

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

    def update_by_public_id(self, public_id: str, user) -> Optional[User]:
        """
        Update a user by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = user.row_version
            existing.firstname = user.firstname
            existing.lastname = user.lastname
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str) -> Optional[User]:
        """
        Delete a user by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
