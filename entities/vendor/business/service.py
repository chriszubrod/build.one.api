# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from entities.vendor.business.model import Vendor
from entities.taxpayer.business.service import TaxpayerService
from entities.vendor_type.business.service import VendorTypeService
from entities.vendor.persistence.repo import VendorRepository


class VendorService:
    """
    Service for Vendor entity business operations.
    """

    def __init__(self, repo: Optional[VendorRepository] = None):
        """Initialize the VendorService."""
        self.repo = repo or VendorRepository()

    def create(self, *, tenant_id: int = 1, name: str, abbreviation: Optional[str] = None, taxpayer_public_id: Optional[str] = None, vendor_type_public_id: Optional[str] = None, is_draft: bool = True) -> Vendor:
        """
        Create a new vendor.

        Args:
            tenant_id: Tenant ID for multi-tenant isolation (default: 1)
            name: Vendor name (required)
            abbreviation: Vendor abbreviation
            taxpayer_public_id: Optional taxpayer public ID
            vendor_type_public_id: Optional vendor type public ID
            is_draft: Whether vendor is in draft state
        """
        if not name or not name.strip():
            raise ValueError("Vendor name is required.")
        name = name.strip()

        existing = self.read_by_name(name=name)
        if existing:
            raise ValueError(f"Vendor with name '{name}' already exists.")

        taxpayer_id = None
        vendor_type_id = None
        if taxpayer_public_id:
            taxpayer = TaxpayerService().read_by_public_id(public_id=taxpayer_public_id)
            if taxpayer:
                taxpayer_id = taxpayer.id
        if vendor_type_public_id:
            vendor_type = VendorTypeService().read_by_public_id(public_id=vendor_type_public_id)
            if vendor_type:
                vendor_type_id = vendor_type.id

        return self.repo.create(tenant_id=tenant_id, name=name, abbreviation=abbreviation, taxpayer_id=taxpayer_id, vendor_type_id=vendor_type_id, is_draft=is_draft)

    def read_all(self) -> list[Vendor]:
        """
        Read all vendors (excludes soft-deleted).
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

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str = None,
        name: str = None,
        abbreviation: str = None,
        taxpayer_public_id: str = None,
        vendor_type_public_id: str = None,
        is_draft: bool = None,
    ) -> Optional[Vendor]:
        """
        Update a vendor by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        # Use provided row_version or keep existing
        if row_version is not None:
            existing.row_version = row_version

        # Check for duplicate name if name is being changed
        if name is not None and name != existing.name:
            duplicate = self.read_by_name(name=name)
            if duplicate and duplicate.public_id != public_id:
                raise ValueError(f"Vendor with name '{name}' already exists.")

        if name is not None:
            existing.name = name
        if abbreviation is not None:
            existing.abbreviation = abbreviation
        if is_draft is not None:
            existing.is_draft = is_draft

        # Handle taxpayer FK — empty string clears, valid public_id sets
        if taxpayer_public_id is not None:
            if taxpayer_public_id == '':
                existing.taxpayer_id = None
            else:
                taxpayer = TaxpayerService().read_by_public_id(public_id=taxpayer_public_id)
                if taxpayer:
                    existing.taxpayer_id = int(taxpayer.id)
                else:
                    existing.taxpayer_id = None

        # Handle vendor_type FK — empty string clears, valid public_id sets
        if vendor_type_public_id is not None:
            if vendor_type_public_id == '':
                existing.vendor_type_id = None
            else:
                vendor_type = VendorTypeService().read_by_public_id(public_id=vendor_type_public_id)
                if vendor_type:
                    existing.vendor_type_id = int(vendor_type.id)
                else:
                    existing.vendor_type_id = None

        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[Vendor]:
        """
        Soft delete a vendor by public ID.

        Sets IsDeleted = 1. All child entity FK relationships (Bill, Expense,
        Contact, etc.) are preserved — no cascade needed.

        TODO: In Phase 10, validate tenant_id matches record's tenant
        """
        logger = logging.getLogger(__name__)

        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        logger.info(f"Soft deleting vendor {public_id} (id={existing.id})")
        return self.repo.soft_delete_by_public_id(public_id=public_id)
