# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from modules.module.business.model import Module
from modules.module.persistence.repo import ModuleRepository


class ModuleService:
    """
    Service for Module entity business operations.
    """

    def __init__(self, repo: Optional[ModuleRepository] = None):
        """Initialize the ModuleService."""
        self.repo = repo or ModuleRepository()

    def create(self, *, name: str, route: str) -> Module:
        """
        Create a new module.
        """
        return self.repo.create(name=name, route=route)

    def read_all(self) -> list[Module]:
        """
        Read all modules.
        """
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[Module]:
        """
        Read a module by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[Module]:
        """
        Read a module by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_name(self, name: str) -> Optional[Module]:
        """
        Read a module by name.
        """
        return self.repo.read_by_name(name)

    def update_by_public_id(self, public_id: str, module) -> Optional[Module]:
        """
        Update a module by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = module.row_version
            existing.name = module.name
            existing.route = module.route
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str) -> Optional[Module]:
        """
        Delete a module by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
