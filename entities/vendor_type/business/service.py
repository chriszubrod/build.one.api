# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.vendor_type.business.model import VendorType
from entities.vendor_type.persistence.repo import VendorTypeRepository


class VendorTypeService:
    """
    Service for VendorType entity business operations.
    """

    def __init__(self, repo: Optional[VendorTypeRepository] = None):
        """Initialize the VendorTypeService."""
        self.repo = repo or VendorTypeRepository()

    def create(self, *, tenant_id: int = None, name: Optional[str], description: Optional[str]) -> VendorType:
        """
        Create a new vendor type.
        """
        # TODO: In Phase 10, use tenant_id for tenant isolation
        return self.repo.create(name=name, description=description)

    def read_all(self) -> list[VendorType]:
        """
        Read all vendor types.
        """
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[VendorType]:
        """
        Read a vendor type by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[VendorType]:
        """
        Read a vendor type by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_name(self, name: str) -> Optional[VendorType]:
        """
        Read a vendor type by name.
        """
        return self.repo.read_by_name(name)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        name: str = None,
        description: str = None,
    ) -> Optional[VendorType]:
        """
        Update a vendor type by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if name is not None:
                existing.name = name
            if description is not None:
                existing.description = description
            return self.repo.update_by_id(existing)
        return None

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[VendorType]:
        """
        Delete a vendor type by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
