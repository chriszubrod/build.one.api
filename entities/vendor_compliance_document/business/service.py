# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.attachment.business.service import AttachmentService
from entities.vendor.business.service import VendorService
from entities.vendor_compliance_document.business.model import VendorComplianceDocument
from entities.vendor_compliance_document.persistence.repo import VendorComplianceDocumentRepository
from shared.authz import current_user_id


class VendorComplianceDocumentService:
    """
    Service for VendorComplianceDocument entity business operations.
    """

    def __init__(self, repo: Optional[VendorComplianceDocumentRepository] = None):
        """Initialize the VendorComplianceDocumentService."""
        self.repo = repo or VendorComplianceDocumentRepository()

    def create(
        self,
        *,
        tenant_id: int = 1,
        vendor_public_id: str,
        document_type: str,
        issuing_authority: Optional[str] = None,
        document_number: Optional[str] = None,
        classification: Optional[str] = None,
        issue_date: Optional[str] = None,
        expiry_date: Optional[str] = None,
        attachment_public_id: Optional[str] = None,
        verification_status: str = "Received",
    ) -> VendorComplianceDocument:
        """
        Create a new vendor compliance document.
        """
        # TODO: In Phase 10, use tenant_id for tenant isolation
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor or not vendor.id:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found")

        attachment_id = None
        if attachment_public_id:
            attachment = AttachmentService().read_by_public_id(public_id=attachment_public_id)
            if not attachment or not attachment.id:
                raise ValueError(f"Attachment with public_id '{attachment_public_id}' not found")
            attachment_id = int(attachment.id)

        return self.repo.create(
            vendor_id=int(vendor.id),
            document_type=document_type,
            issuing_authority=issuing_authority,
            document_number=document_number,
            classification=classification,
            issue_date=issue_date,
            expiry_date=expiry_date,
            attachment_id=attachment_id,
            verification_status=verification_status,
            created_by_user_id=current_user_id.get(),
        )

    def read_by_id(self, id: str) -> Optional[VendorComplianceDocument]:
        """
        Read a vendor compliance document by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[VendorComplianceDocument]:
        """
        Read a vendor compliance document by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_vendor_id(self, vendor_id: int) -> list[VendorComplianceDocument]:
        """
        Read vendor compliance documents by vendor ID.
        """
        return self.repo.read_by_vendor_id(vendor_id=vendor_id)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = 1,
        row_version: str,
        issuing_authority: Optional[str] = None,
        document_number: Optional[str] = None,
        classification: Optional[str] = None,
        issue_date: Optional[str] = None,
        expiry_date: Optional[str] = None,
        attachment_public_id: Optional[str] = None,
        verification_status: Optional[str] = None,
    ) -> Optional[VendorComplianceDocument]:
        """
        Update a vendor compliance document by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        existing.row_version = row_version
        if issuing_authority is not None:
            existing.issuing_authority = issuing_authority
        if document_number is not None:
            existing.document_number = document_number
        if classification is not None:
            existing.classification = classification
        if issue_date is not None:
            existing.issue_date = issue_date
        if expiry_date is not None:
            existing.expiry_date = expiry_date
        if verification_status is not None:
            existing.verification_status = verification_status
        if attachment_public_id is not None:
            attachment = AttachmentService().read_by_public_id(public_id=attachment_public_id)
            if not attachment or not attachment.id:
                raise ValueError(f"Attachment with public_id '{attachment_public_id}' not found")
            existing.attachment_id = int(attachment.id)

        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = 1) -> Optional[VendorComplianceDocument]:
        """
        Delete a vendor compliance document by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing and existing.id:
            if self.repo.delete_by_id(int(existing.id)):
                return existing
        return None
