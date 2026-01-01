# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from modules.vendor.business.model import Vendor
from modules.taxpayer.business.service import TaxpayerService
from modules.vendor_type.business.service import VendorTypeService
from modules.vendor.persistence.repo import VendorRepository


class VendorService:
    """
    Service for Vendor entity business operations.
    """

    def __init__(self, repo: Optional[VendorRepository] = None):
        """Initialize the VendorService."""
        self.repo = repo or VendorRepository()

    def create(self, *, name: Optional[str], abbreviation: Optional[str], taxpayer_public_id: Optional[str] = None, vendor_type_public_id: Optional[str] = None, is_draft: bool = True) -> Vendor:
        """
        Create a new vendor.
        """
        if name:
            existing = self.read_by_name(name=name)
            if existing:
                raise ValueError(f"Vendor with name '{name}' already exists.")
        taxpayer = TaxpayerService().read_by_public_id(public_id=taxpayer_public_id)
        vendor_type = VendorTypeService().read_by_public_id(public_id=vendor_type_public_id)
        taxpayer_id = None
        vendor_type_id = None
        if taxpayer:
            taxpayer_id = taxpayer.id
        if vendor_type:
            vendor_type_id = vendor_type.id
        return self.repo.create(name=name, abbreviation=abbreviation, taxpayer_id=taxpayer_id, vendor_type_id=vendor_type_id, is_draft=is_draft)

    def read_all(self) -> list[Vendor]:
        """
        Read all vendors.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[Vendor]:
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
        if not existing:
            return None
        
        # Use provided row_version or refresh to get latest
        if hasattr(vendor, 'row_version') and vendor.row_version:
            existing.row_version = vendor.row_version
        # If no row_version provided, use the one from existing (already set)
        
        # Check for duplicate name if name is being changed
        if hasattr(vendor, 'name') and vendor.name is not None and vendor.name != existing.name:
            duplicate = self.read_by_name(name=vendor.name)
            if duplicate and duplicate.public_id != public_id:
                raise ValueError(f"Vendor with name '{vendor.name}' already exists.")
        
        if hasattr(vendor, 'name') and vendor.name is not None:
            existing.name = vendor.name
        if hasattr(vendor, 'abbreviation') and vendor.abbreviation is not None:
            existing.abbreviation = vendor.abbreviation
        if hasattr(vendor, 'is_draft') and vendor.is_draft is not None:
            existing.is_draft = vendor.is_draft
        if hasattr(vendor, 'taxpayer_public_id') and vendor.taxpayer_public_id:
            taxpayer = TaxpayerService().read_by_public_id(public_id=vendor.taxpayer_public_id)
            if taxpayer:
                existing.taxpayer_id = int(taxpayer.id) if taxpayer.id else None
        if hasattr(vendor, 'vendor_type_public_id') and vendor.vendor_type_public_id:
            vendor_type = VendorTypeService().read_by_public_id(public_id=vendor.vendor_type_public_id)
            if vendor_type:
                existing.vendor_type_id = int(vendor_type.id) if vendor_type.id else None
        
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str) -> Optional[Vendor]:
        """
        Delete a vendor by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(id=existing.id)
        return None
