# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from modules.vendor_address.business.model import VendorAddress
from modules.vendor_address.persistence.repo import VendorAddressRepository


class VendorAddressService:
    """
    Service for VendorAddress entity business operations.
    """

    def __init__(self, repo: Optional[VendorAddressRepository] = None):
        """Initialize the VendorAddressService."""
        self.repo = repo or VendorAddressRepository()

    def create(self, *, vendor_id: str, address_id: str, address_type_id: str) -> VendorAddress:
        """
        Create a new vendor address.
        """
        return self.repo.create(vendor_id=vendor_id, address_id=address_id, address_type_id=address_type_id)

    def read_all(self) -> list[VendorAddress]:
        """
        Read all vendor addresses.
        """
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[VendorAddress]:
        """
        Read a vendor address by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[VendorAddress]:
        """
        Read a vendor address by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_vendor_id(self, vendor_id: str) -> Optional[VendorAddress]:
        """
        Read a vendor address by vendor ID.
        """
        return self.repo.read_by_vendor_id(vendor_id=vendor_id)

    def read_by_address_id(self, address_id: str) -> Optional[VendorAddress]:
        """
        Read a vendor address by address ID.
        """
        return self.repo.read_by_address_id(address_id=address_id)

    def read_by_address_type_id(self, address_type_id: str) -> Optional[VendorAddress]:
        """
        Read a vendor address by address type ID.
        """
        return self.repo.read_by_address_type_id(address_type_id=address_type_id)

    def update_by_public_id(self, public_id: str, vendor_address: VendorAddress) -> Optional[VendorAddress]:
        """
        Update a vendor address by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = vendor_address.row_version
            existing.vendor_id = vendor_address.vendor_id
            existing.address_id = vendor_address.address_id
            existing.address_type_id = vendor_address.address_type_id
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str) -> Optional[VendorAddress]:
        """
        Delete a vendor address by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
