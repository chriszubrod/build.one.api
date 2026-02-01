# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from entities.vendor.business.model import Vendor
from entities.taxpayer.business.service import TaxpayerService
from entities.taxpayer.persistence.repo import TaxpayerRepository
from entities.vendor_type.business.service import VendorTypeService
from entities.vendor.persistence.repo import VendorRepository
from entities.vendor_address.persistence.repo import VendorAddressRepository
from entities.taxpayer_attachment.business.service import TaxpayerAttachmentService
from entities.taxpayer_attachment.persistence.repo import TaxpayerAttachmentRepository
from entities.attachment.business.service import AttachmentService
from shared.storage import AzureBlobStorage


class VendorService:
    """
    Service for Vendor entity business operations.
    """

    def __init__(self, repo: Optional[VendorRepository] = None):
        """Initialize the VendorService."""
        self.repo = repo or VendorRepository()

    def create(self, *, tenant_id: int = 1, name: Optional[str], abbreviation: Optional[str], taxpayer_public_id: Optional[str] = None, vendor_type_public_id: Optional[str] = None, is_draft: bool = True) -> Vendor:
        """
        Create a new vendor.
        
        Args:
            tenant_id: Tenant ID for multi-tenant isolation (default: 1)
            name: Vendor name
            abbreviation: Vendor abbreviation
            taxpayer_public_id: Optional taxpayer public ID
            vendor_type_public_id: Optional vendor type public ID
            is_draft: Whether vendor is in draft state
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
        return self.repo.create(tenant_id=tenant_id, name=name, abbreviation=abbreviation, taxpayer_id=taxpayer_id, vendor_type_id=vendor_type_id, is_draft=is_draft)

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
        if taxpayer_public_id is not None:
            taxpayer = TaxpayerService().read_by_public_id(public_id=taxpayer_public_id)
            if taxpayer:
                existing.taxpayer_id = int(taxpayer.id) if taxpayer.id else None
        if vendor_type_public_id is not None:
            vendor_type = VendorTypeService().read_by_public_id(public_id=vendor_type_public_id)
            if vendor_type:
                existing.vendor_type_id = int(vendor_type.id) if vendor_type.id else None
        
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[Vendor]:
        """
        Delete a vendor by public ID.
        
        TODO: In Phase 10, validate tenant_id matches record's tenant
        
        Process:
        1. Search for existing vendor record using public_id
        2. If found, delete all associated VendorAddress records by vendor database id
        3. If vendor has a taxpayer_id:
           a. Get all TaxpayerAttachment records for the taxpayer
           b. For each TaxpayerAttachment:
              - Get the Attachment record
              - Delete the file from Azure Blob Storage (if blob_url exists)
              - Delete the Attachment record from database
              - Delete the TaxpayerAttachment record
           c. Delete the Taxpayer record
        4. Delete the vendor by database id
        
        This will cascade delete:
        - VendorAddress records
        - TaxpayerAttachment records
        - Attachment records (and files from Azure Blob Storage)
        - Taxpayer record
        - Vendor record
        """
        logger = logging.getLogger(__name__)
        
        # Step 1: Search for existing vendor record using public_id
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None
        
        # Step 2: Delete all vendor addresses for this vendor first (by vendor database id)
        if existing.id:
            try:
                vendor_address_repo = VendorAddressRepository()
                vendor_address_repo.delete_by_vendor_id(vendor_id=existing.id)
            except Exception as e:
                logger.warning(f"Error deleting vendor addresses for vendor {existing.id}: {e}")
        
        # Step 3: Delete taxpayer-related records if taxpayer exists
        if existing.taxpayer_id:
            try:
                # Get the taxpayer to access its public_id
                taxpayer = TaxpayerService().read_by_id(id=existing.taxpayer_id)
                if taxpayer and taxpayer.public_id:
                    # Step 3a: Get all TaxpayerAttachment records for this taxpayer
                    taxpayer_attachment_service = TaxpayerAttachmentService()
                    taxpayer_attachments = taxpayer_attachment_service.read_by_taxpayer_id(taxpayer_public_id=taxpayer.public_id)
                    
                    # Step 3b: Delete each attachment and its link
                    attachment_service = AttachmentService()
                    # Initialize storage once (may fail if config is missing, handle gracefully)
                    storage = None
                    try:
                        storage = AzureBlobStorage()
                    except Exception as e:
                        logger.warning(f"Could not initialize Azure Blob Storage for file deletion: {e}")
                    
                    for ta in taxpayer_attachments:
                        try:
                            if ta.attachment_id:
                                # Get the attachment record
                                attachment = attachment_service.read_by_id(id=ta.attachment_id)
                                if attachment:
                                    # Delete from Azure Blob Storage if blob_url exists
                                    if attachment.blob_url and storage:
                                        try:
                                            storage.delete_file(attachment.blob_url)
                                        except Exception as e:
                                            logger.warning(f"Error deleting blob {attachment.blob_url} for attachment {attachment.id}: {e}")
                                    
                                    # Delete the Attachment record
                                    try:
                                        attachment_service.delete_by_public_id(public_id=attachment.public_id)
                                    except Exception as e:
                                        logger.warning(f"Error deleting attachment {attachment.id}: {e}")
                            
                            # Delete the TaxpayerAttachment record
                            if ta.id:
                                try:
                                    taxpayer_attachment_repo = TaxpayerAttachmentRepository()
                                    taxpayer_attachment_repo.delete_by_id(id=ta.id)
                                except Exception as e:
                                    logger.warning(f"Error deleting taxpayer attachment {ta.id}: {e}")
                        except Exception as e:
                            logger.warning(f"Error processing taxpayer attachment {ta.id if ta.id else 'unknown'}: {e}")
                
                # Step 3c: Delete the Taxpayer record
                taxpayer_repo = TaxpayerRepository()
                taxpayer_repo.delete_by_id(id=existing.taxpayer_id)
            except Exception as e:
                logger.warning(f"Error deleting taxpayer {existing.taxpayer_id} for vendor {existing.id}: {e}")
        
        # Step 4: Delete the vendor by database id
        return self.repo.delete_by_id(id=existing.id)
