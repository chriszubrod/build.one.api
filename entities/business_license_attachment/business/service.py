# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.business_license_attachment.business.model import BusinessLicenseAttachment
from entities.business_license_attachment.persistence.repo import BusinessLicenseAttachmentRepository
from entities.business_license.business.service import BusinessLicenseService
from entities.attachment.business.service import AttachmentService


class BusinessLicenseAttachmentService:
    """
    Service for BusinessLicenseAttachment entity business operations.
    """

    def __init__(self, repo: Optional[BusinessLicenseAttachmentRepository] = None):
        """Initialize the BusinessLicenseAttachmentService."""
        self.repo = repo or BusinessLicenseAttachmentRepository()

    def create(
        self,
        *,
        tenant_id: int = None,
        business_license_public_id: str,
        attachment_public_id: str,
    ) -> BusinessLicenseAttachment:
        """
        Create a new business license attachment link.

        Ensures uniqueness: A BusinessLicense can have many different Attachments,
        but each unique (BusinessLicenseId, AttachmentId) combination can only have
        ONE BusinessLicenseAttachment record. If a link already exists, returns the
        existing record instead of creating a duplicate.

        Raises:
            ValueError: If business license or attachment not found
        """
        # TODO: In Phase 10, use tenant_id for tenant isolation
        business_license = BusinessLicenseService().read_by_public_id(
            public_id=business_license_public_id
        )
        attachment = AttachmentService().read_by_public_id(public_id=attachment_public_id)

        if not business_license or not business_license.id:
            raise ValueError(
                f"Business license with public_id '{business_license_public_id}' not found"
            )
        if not attachment or not attachment.id:
            raise ValueError(f"Attachment with public_id '{attachment_public_id}' not found")

        business_license_id = int(business_license.id)
        attachment_id = int(attachment.id)

        existing_attachments = self.repo.read_by_business_license_id(
            business_license_id=business_license_id
        )
        for existing in existing_attachments:
            if existing.attachment_id and int(existing.attachment_id) == attachment_id:
                return existing

        return self.repo.create(
            business_license_id=business_license_id,
            attachment_id=attachment_id,
        )

    def read_all(self) -> list[BusinessLicenseAttachment]:
        """
        Read all business license attachments.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[BusinessLicenseAttachment]:
        """
        Read a business license attachment by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[BusinessLicenseAttachment]:
        """
        Read a business license attachment by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_business_license_id(self, business_license_public_id: str) -> list[BusinessLicenseAttachment]:
        """
        Read business license attachments by business license public ID.
        """
        business_license = BusinessLicenseService().read_by_public_id(
            public_id=business_license_public_id
        )
        if not business_license or not business_license.id:
            return []

        business_license_id = int(business_license.id)
        return self.repo.read_by_business_license_id(business_license_id=business_license_id)

    def delete_by_public_id(
        self, public_id: str, *, tenant_id: int = None
    ) -> Optional[BusinessLicenseAttachment]:
        """
        Delete a business license attachment by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing and existing.id:
            return self.repo.delete_by_id(existing.id)
        return None
