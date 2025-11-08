# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from modules.vendor.business.model import Vendor
from modules.vendor.persistence.repo import VendorRepository


class VendorService:
    """
    Service for Vendor entity business operations.
    """

    def __init__(self, repo: Optional[VendorRepository] = None):
        """Initialize the VendorService."""
        self.repo = repo or VendorRepository()

    def create(self, *, name: Optional[str], abbreviation: Optional[str]) -> Vendor:
        """
        Create a new vendor.
        """
        return self.repo.create(name=name, abbreviation=abbreviation)

    def read_all(self) -> list[Vendor]:
        """
        Read all vendors.
        """
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[Vendor]:
        """
        Read a vendor by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[Vendor]:
        """
        Read a vendor by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_name(self, name: str) -> Optional[Vendor]:
        """
        Read a vendor by name.
        """
        return self.repo.read_by_name(name)

    def update_by_public_id(self, public_id: str, vendor) -> Optional[Vendor]:
        """
        Update a vendor by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = vendor.row_version
            existing.name = vendor.name
            existing.abbreviation = vendor.abbreviation
            return self.repo.update_by_id(existing)
        return None

    def delete_by_public_id(self, public_id: str) -> Optional[Vendor]:
        """
        Delete a vendor by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
