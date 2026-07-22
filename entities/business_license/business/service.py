# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.business_license.business.model import BusinessLicense
from entities.business_license.persistence.repo import BusinessLicenseRepository
from entities.vendor.business.service import VendorService
from shared.authz import current_user_id


class BusinessLicenseService:
    """
    Service for BusinessLicense entity business operations.
    """

    def __init__(self, repo: Optional[BusinessLicenseRepository] = None):
        """Initialize the BusinessLicenseService."""
        self.repo = repo or BusinessLicenseRepository()

    def create(
        self,
        *,
        tenant_id: int = None,
        vendor_public_id: str,
        license_number: Optional[str] = None,
        issuing_authority: Optional[str] = None,
        issue_date: Optional[str] = None,
        expiry_date: Optional[str] = None,
        verification_status: str = "Received",
    ) -> BusinessLicense:
        """
        Create a new business license for a vendor.
        """
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor or not vendor.id:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found")

        # TODO: In Phase 10, use tenant_id for tenant isolation
        return self.repo.create(
            vendor_id=int(vendor.id),
            license_number=license_number,
            issuing_authority=issuing_authority,
            issue_date=issue_date,
            expiry_date=expiry_date,
            verification_status=verification_status,
            created_by_user_id=current_user_id.get(),
        )

    def read_all(self) -> list[BusinessLicense]:
        """
        Read all business licenses.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[BusinessLicense]:
        """
        Read a business license by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[BusinessLicense]:
        """
        Read a business license by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_vendor_id(self, vendor_id: int) -> list[BusinessLicense]:
        """
        Read business licenses by vendor ID.
        """
        return self.repo.read_by_vendor_id(vendor_id=vendor_id)

    def read_by_vendor_public_id(self, vendor_public_id: str) -> list[BusinessLicense]:
        """
        Read business licenses by vendor public ID.
        """
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor or not vendor.id:
            return []
        return self.read_by_vendor_id(vendor_id=int(vendor.id))

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        license_number: Optional[str] = None,
        issuing_authority: Optional[str] = None,
        issue_date: Optional[str] = None,
        expiry_date: Optional[str] = None,
        verification_status: Optional[str] = None,
    ) -> Optional[BusinessLicense]:
        """
        Update a business license by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if license_number is not None:
                existing.license_number = license_number
            if issuing_authority is not None:
                existing.issuing_authority = issuing_authority
            if issue_date is not None:
                existing.issue_date = issue_date
            if expiry_date is not None:
                existing.expiry_date = expiry_date
            if verification_status is not None:
                existing.verification_status = verification_status
            return self.repo.update_by_id(existing)
        return None

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[BusinessLicense]:
        """
        Delete a business license by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
