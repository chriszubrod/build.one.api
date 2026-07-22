# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.contractors_license.business.model import ContractorsLicense
from entities.contractors_license.persistence.repo import ContractorsLicenseRepository
from entities.vendor.business.service import VendorService
from shared.authz import current_user_id


class ContractorsLicenseService:
    """
    Service for ContractorsLicense entity business operations.
    """

    def __init__(self, repo: Optional[ContractorsLicenseRepository] = None):
        """Initialize the ContractorsLicenseService."""
        self.repo = repo or ContractorsLicenseRepository()

    def create(
        self,
        *,
        tenant_id: int = None,
        vendor_public_id: str,
        license_number: Optional[str] = None,
        issuing_authority: Optional[str] = None,
        classification: Optional[str] = None,
        issue_date: Optional[str] = None,
        expiry_date: Optional[str] = None,
        verification_status: str = "Received",
    ) -> ContractorsLicense:
        """
        Create a new contractors license for a vendor.
        """
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor or not vendor.id:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found")

        # TODO: In Phase 10, use tenant_id for tenant isolation
        return self.repo.create(
            vendor_id=int(vendor.id),
            license_number=license_number,
            issuing_authority=issuing_authority,
            classification=classification,
            issue_date=issue_date,
            expiry_date=expiry_date,
            verification_status=verification_status,
            created_by_user_id=current_user_id.get(),
        )

    def read_all(self) -> list[ContractorsLicense]:
        """
        Read all contractors licenses.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[ContractorsLicense]:
        """
        Read a contractors license by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[ContractorsLicense]:
        """
        Read a contractors license by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_vendor_id(self, vendor_id: int) -> list[ContractorsLicense]:
        """
        Read contractors licenses by vendor ID.
        """
        return self.repo.read_by_vendor_id(vendor_id=vendor_id)

    def read_by_vendor_public_id(self, vendor_public_id: str) -> list[ContractorsLicense]:
        """
        Read contractors licenses by vendor public ID.
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
        classification: Optional[str] = None,
        issue_date: Optional[str] = None,
        expiry_date: Optional[str] = None,
        verification_status: Optional[str] = None,
    ) -> Optional[ContractorsLicense]:
        """
        Update a contractors license by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if license_number is not None:
                existing.license_number = license_number
            if issuing_authority is not None:
                existing.issuing_authority = issuing_authority
            if classification is not None:
                existing.classification = classification
            if issue_date is not None:
                existing.issue_date = issue_date
            if expiry_date is not None:
                existing.expiry_date = expiry_date
            if verification_status is not None:
                existing.verification_status = verification_status
            return self.repo.update_by_id(existing)
        return None

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[ContractorsLicense]:
        """
        Delete a contractors license by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
