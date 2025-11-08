# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from mappings.vendor_qbo_vendor.business.model import MapVendorQboVendor
from mappings.vendor_qbo_vendor.persistence.repo import MapVendorQboVendorRepository


class MapVendorQboVendorService:
    """
    Service for MapVendorQboVendor entity business operations.
    """

    def __init__(self, repo: Optional[MapVendorQboVendorRepository] = None):
        """Initialize the MapVendorQboVendorService."""
        self.repo = repo or MapVendorQboVendorRepository()

    def create(self, *, vendor_id: Optional[str], qbo_vendor_id: Optional[str]) -> MapVendorQboVendor:
        """
        Create a new map vendor qbo vendor record.
        """
        return self.repo.create(vendor_id=vendor_id, qbo_vendor_id=qbo_vendor_id)

    def read_all(self) -> list[MapVendorQboVendor]:
        """
        Read all map vendor qbo vendor records.
        """
        return self.repo.read_all()

    def read_by_id(self, id: str) -> Optional[MapVendorQboVendor]:
        """
        Read a map vendor qbo vendor record by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[MapVendorQboVendor]:
        """
        Read a map vendor qbo vendor record by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_vendor_id(self, vendor_id: str) -> Optional[MapVendorQboVendor]:
        """
        Read a map vendor qbo vendor record by vendor ID.
        """
        return self.repo.read_by_vendor_id(vendor_id)
    
    def read_by_qbo_vendor_id(self, qbo_vendor_id: str) -> Optional[MapVendorQboVendor]:
        """
        Read a map vendor qbo vendor record by qbo vendor ID.
        """
        return self.repo.read_by_qbo_vendor_id(qbo_vendor_id)

    def update_by_public_id(self, public_id: str, vendor_id: str, qbo_vendor_id: str) -> Optional[MapVendorQboVendor]:
        """
        Update a map vendor qbo vendor record by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = vendor_id
            existing.vendor_id = vendor_id
            existing.qbo_vendor_id = qbo_vendor_id
            return self.repo.update_by_id(existing)
        return None

    def delete_by_public_id(self, public_id: str) -> Optional[MapVendorQboVendor]:
        """
        Delete a map vendor qbo vendor record by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
