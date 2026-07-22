# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.certificate_of_insurance.business.model import CertificateOfInsurance
from entities.certificate_of_insurance.persistence.repo import CertificateOfInsuranceRepository
from entities.vendor.business.service import VendorService
from shared.authz import current_user_id


class CertificateOfInsuranceService:
    """
    Service for CertificateOfInsurance entity business operations.
    """

    def __init__(self, repo: Optional[CertificateOfInsuranceRepository] = None):
        """Initialize the CertificateOfInsuranceService."""
        self.repo = repo or CertificateOfInsuranceRepository()

    def create(
        self,
        *,
        tenant_id: int = None,
        vendor_public_id: str,
        issuing_authority: Optional[str] = None,
        issue_date: Optional[str] = None,
        attachment_id: Optional[int] = None,
        verification_status: str = "Received",
    ) -> CertificateOfInsurance:
        """
        Create a new certificate of insurance for a vendor.
        """
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor or not vendor.id:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found")

        # TODO: In Phase 10, use tenant_id for tenant isolation
        return self.repo.create(
            vendor_id=int(vendor.id),
            issuing_authority=issuing_authority,
            issue_date=issue_date,
            attachment_id=attachment_id,
            verification_status=verification_status,
            created_by_user_id=current_user_id.get(),
        )

    def read_all(self) -> list[CertificateOfInsurance]:
        """
        Read all certificates of insurance.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[CertificateOfInsurance]:
        """
        Read a certificate of insurance by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[CertificateOfInsurance]:
        """
        Read a certificate of insurance by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_vendor_id(self, vendor_id: int) -> list[CertificateOfInsurance]:
        """
        Read certificates of insurance by vendor ID.
        """
        return self.repo.read_by_vendor_id(vendor_id=vendor_id)

    def read_by_vendor_public_id(self, vendor_public_id: str) -> list[CertificateOfInsurance]:
        """
        Read certificates of insurance by vendor public ID.
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
        issuing_authority: Optional[str] = None,
        issue_date: Optional[str] = None,
        attachment_id: Optional[int] = None,
        verification_status: Optional[str] = None,
    ) -> Optional[CertificateOfInsurance]:
        """
        Update a certificate of insurance by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if issuing_authority is not None:
                existing.issuing_authority = issuing_authority
            if issue_date is not None:
                existing.issue_date = issue_date
            if attachment_id is not None:
                existing.attachment_id = attachment_id
            if verification_status is not None:
                existing.verification_status = verification_status
            return self.repo.update_by_id(existing)
        return None

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[CertificateOfInsurance]:
        """
        Delete a certificate of insurance by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
