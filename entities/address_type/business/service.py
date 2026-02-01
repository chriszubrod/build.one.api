# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.address_type.business.model import AddressType
from entities.address_type.persistence.repo import AddressTypeRepository


class AddressTypeService:
    """
    Service for AddressType entity business operations.
    """

    def __init__(self, repo: Optional[AddressTypeRepository] = None):
        """Initialize the AddressTypeService."""
        self.repo = repo or AddressTypeRepository()

    def create(self, *, name: str, description: str, display_order: int) -> AddressType:
        """
        Create a new address type.
        """
        return self.repo.create(name=name, description=description, display_order=display_order)

    def read_all(self) -> list[AddressType]:
        """
        Read all address types.
        """
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[AddressType]:
        """
        Read an address type by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[AddressType]:
        """
        Read an address type by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_name(self, name: str) -> Optional[AddressType]:
        """
        Read an address type by name.
        """
        return self.repo.read_by_name(name=name)

    def update_by_public_id(self, public_id: str, address_type: AddressType) -> Optional[AddressType]:
        """
        Update an address type by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = address_type.row_version
            existing.name = address_type.name
            existing.description = address_type.description
            existing.display_order = address_type.display_order
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str) -> Optional[AddressType]:
        """
        Delete an address type by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
