# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from services.taxpayer_attachment.business.model import TaxpayerAttachment
from services.taxpayer_attachment.persistence.repo import TaxpayerAttachmentRepository
from services.taxpayer.business.service import TaxpayerService
from services.attachment.business.service import AttachmentService


class TaxpayerAttachmentService:
    """
    Service for TaxpayerAttachment entity business operations.
    """

    def __init__(self, repo: Optional[TaxpayerAttachmentRepository] = None):
        """Initialize the TaxpayerAttachmentService."""
        self.repo = repo or TaxpayerAttachmentRepository()

    def create(self, *, tenant_id: int = None, taxpayer_public_id: str, attachment_public_id: str) -> TaxpayerAttachment:
        """
        Create a new taxpayer attachment link.
        
        Ensures uniqueness: A Taxpayer can have many different Attachments,
        but each unique (TaxpayerId, AttachmentId) combination can only have
        ONE TaxpayerAttachment record. If a link already exists, returns the
        existing record instead of creating a duplicate.
        
        Args:
            taxpayer_public_id: Public ID of the taxpayer
            attachment_public_id: Public ID of the attachment
            
        Returns:
            TaxpayerAttachment: The existing record if duplicate, or newly created record
            
        Raises:
            ValueError: If taxpayer or attachment not found
        """
        # TODO: In Phase 10, use tenant_id for tenant isolation
        # Resolve public IDs to internal IDs
        taxpayer = TaxpayerService().read_by_public_id(public_id=taxpayer_public_id)
        attachment = AttachmentService().read_by_public_id(public_id=attachment_public_id)
        
        if not taxpayer or not taxpayer.id:
            raise ValueError(f"Taxpayer with public_id '{taxpayer_public_id}' not found")
        if not attachment or not attachment.id:
            raise ValueError(f"Attachment with public_id '{attachment_public_id}' not found")
        
        taxpayer_id = int(taxpayer.id)
        attachment_id = int(attachment.id)
        
        # Check if a TaxpayerAttachment already exists for this (TaxpayerId, AttachmentId) pair
        # This ensures only ONE mapping exists per unique combination
        existing_attachments = self.repo.read_by_taxpayer_id(taxpayer_id=taxpayer_id)
        for existing in existing_attachments:
            if existing.attachment_id and int(existing.attachment_id) == attachment_id:
                # Duplicate detected: This (TaxpayerId, AttachmentId) combination already exists
                # Return the existing record instead of creating a duplicate
                return existing
        
        # No duplicate found - this is a new (TaxpayerId, AttachmentId) combination
        # Safe to create a new TaxpayerAttachment record
        return self.repo.create(taxpayer_id=taxpayer_id, attachment_id=attachment_id)

    def read_all(self) -> list[TaxpayerAttachment]:
        """
        Read all taxpayer attachments.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[TaxpayerAttachment]:
        """
        Read a taxpayer attachment by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[TaxpayerAttachment]:
        """
        Read a taxpayer attachment by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_taxpayer_id(self, taxpayer_public_id: str) -> list[TaxpayerAttachment]:
        """
        Read taxpayer attachments by taxpayer public ID.
        """
        taxpayer = TaxpayerService().read_by_public_id(public_id=taxpayer_public_id)
        if not taxpayer or not taxpayer.id:
            return []
        
        taxpayer_id = int(taxpayer.id)
        return self.repo.read_by_taxpayer_id(taxpayer_id=taxpayer_id)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[TaxpayerAttachment]:
        """
        Delete a taxpayer attachment by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing and existing.id:
            return self.repo.delete_by_id(existing.id)
        return None

