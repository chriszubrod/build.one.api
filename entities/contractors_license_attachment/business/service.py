# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.contractors_license_attachment.business.model import ContractorsLicenseAttachment
from entities.contractors_license_attachment.persistence.repo import ContractorsLicenseAttachmentRepository
from entities.contractors_license.business.service import ContractorsLicenseService
from entities.attachment.business.service import AttachmentService


class ContractorsLicenseAttachmentService:
    """
    Service for ContractorsLicenseAttachment entity business operations.
    """

    def __init__(self, repo: Optional[ContractorsLicenseAttachmentRepository] = None):
        """Initialize the ContractorsLicenseAttachmentService."""
        self.repo = repo or ContractorsLicenseAttachmentRepository()

    def create(
        self,
        *,
        tenant_id: int = None,
        contractors_license_public_id: str,
        attachment_public_id: str,
    ) -> ContractorsLicenseAttachment:
        """
        Create a new contractors license attachment link.

        Ensures uniqueness: A ContractorsLicense can have many different Attachments,
        but each unique (ContractorsLicenseId, AttachmentId) combination can only have
        ONE ContractorsLicenseAttachment record. If a link already exists, returns the
        existing record instead of creating a duplicate.

        Raises:
            ValueError: If contractors license or attachment not found
        """
        # TODO: In Phase 10, use tenant_id for tenant isolation
        contractors_license = ContractorsLicenseService().read_by_public_id(
            public_id=contractors_license_public_id
        )
        attachment = AttachmentService().read_by_public_id(public_id=attachment_public_id)

        if not contractors_license or not contractors_license.id:
            raise ValueError(
                f"Contractors license with public_id '{contractors_license_public_id}' not found"
            )
        if not attachment or not attachment.id:
            raise ValueError(f"Attachment with public_id '{attachment_public_id}' not found")

        contractors_license_id = int(contractors_license.id)
        attachment_id = int(attachment.id)

        existing_attachments = self.repo.read_by_contractors_license_id(
            contractors_license_id=contractors_license_id
        )
        for existing in existing_attachments:
            if existing.attachment_id and int(existing.attachment_id) == attachment_id:
                return existing

        return self.repo.create(
            contractors_license_id=contractors_license_id,
            attachment_id=attachment_id,
        )

    def read_all(self) -> list[ContractorsLicenseAttachment]:
        """
        Read all contractors license attachments.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[ContractorsLicenseAttachment]:
        """
        Read a contractors license attachment by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[ContractorsLicenseAttachment]:
        """
        Read a contractors license attachment by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_contractors_license_id(
        self, contractors_license_public_id: str
    ) -> list[ContractorsLicenseAttachment]:
        """
        Read contractors license attachments by contractors license public ID.
        """
        contractors_license = ContractorsLicenseService().read_by_public_id(
            public_id=contractors_license_public_id
        )
        if not contractors_license or not contractors_license.id:
            return []

        contractors_license_id = int(contractors_license.id)
        return self.repo.read_by_contractors_license_id(
            contractors_license_id=contractors_license_id
        )

    def delete_by_public_id(
        self, public_id: str, *, tenant_id: int = None
    ) -> Optional[ContractorsLicenseAttachment]:
        """
        Delete a contractors license attachment by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing and existing.id:
            return self.repo.delete_by_id(existing.id)
        return None
