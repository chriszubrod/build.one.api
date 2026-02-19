# Python Standard Library Imports
import logging
import os
import uuid
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.attachable.connector.attachment.business.model import AttachableAttachment
from integrations.intuit.qbo.attachable.connector.attachment.persistence.repo import AttachableAttachmentRepository
from integrations.intuit.qbo.attachable.business.model import QboAttachable
from integrations.intuit.qbo.attachable.persistence.repo import QboAttachableRepository
from integrations.intuit.qbo.attachable.external.client import QboAttachableClient
from integrations.intuit.qbo.auth.business.service import QboAuthService
from entities.attachment.business.service import AttachmentService
from entities.attachment.business.model import Attachment
from shared.storage import AzureBlobStorage, AzureBlobStorageError
from shared.pdf_utils import ensure_pdf

logger = logging.getLogger(__name__)


class AttachableAttachmentConnector:
    """
    Connector service for synchronization between QboAttachable and Attachment modules.
    """

    def __init__(
        self,
        mapping_repo: Optional[AttachableAttachmentRepository] = None,
        attachment_service: Optional[AttachmentService] = None,
        auth_service: Optional[QboAuthService] = None,
    ):
        """Initialize the AttachableAttachmentConnector."""
        self.mapping_repo = mapping_repo or AttachableAttachmentRepository()
        self.attachment_service = attachment_service or AttachmentService()
        self.auth_service = auth_service or QboAuthService()

    def sync_from_qbo_attachable(
        self,
        qbo_attachable: QboAttachable,
        realm_id: str,
    ) -> Attachment:
        """
        Sync data from QboAttachable to Attachment module.
        
        This method:
        1. Checks if a mapping already exists
        2. Downloads the file from QBO if needed
        3. Uploads to Azure Blob Storage
        4. Creates or updates the Attachment accordingly
        
        Args:
            qbo_attachable: QboAttachable record from local database
            realm_id: QBO realm ID for API access
        
        Returns:
            Attachment: The synced Attachment record
        """
        # Check for existing mapping
        mapping = self.mapping_repo.read_by_qbo_attachable_id(qbo_attachable.id)
        
        if mapping:
            # Found existing mapping - return the existing Attachment
            attachment = self.attachment_service.read_by_id(mapping.attachment_id)
            if attachment:
                logger.info(f"Found existing Attachment {attachment.id} for QboAttachable {qbo_attachable.id}")
                # Optionally update metadata here if needed
                return attachment
            else:
                # Mapping exists but Attachment not found - recreate
                logger.warning(f"Mapping exists but Attachment {mapping.attachment_id} not found. Creating new.")
                self.mapping_repo.delete_by_id(mapping.id)
                mapping = None
        
        # Download file from QBO
        file_content = self._download_from_qbo(qbo_attachable, realm_id)
        
        if not file_content:
            raise ValueError(f"Failed to download file for QboAttachable {qbo_attachable.id}")
        
        # Ensure PDF: convert images to PDF
        content_type = qbo_attachable.content_type or "application/octet-stream"
        file_name = qbo_attachable.file_name or f"attachment_{qbo_attachable.qbo_id}"
        file_content, content_type, file_extension = ensure_pdf(file_content, content_type, file_name)
        if file_extension == ".pdf":
            file_name = self._ensure_pdf_filename(file_name)
        
        # Calculate file hash
        file_hash = self.attachment_service.calculate_hash(file_content)
        
        # Check for duplicate by hash
        existing_by_hash = self.attachment_service.read_by_hash(file_hash)
        if existing_by_hash:
            logger.info(f"Found existing Attachment by hash for QboAttachable {qbo_attachable.id}")
            # Create mapping to existing attachment
            self._create_mapping(attachment_id=existing_by_hash.id, qbo_attachable_id=qbo_attachable.id)
            return existing_by_hash
        
        # Upload to Azure Blob Storage (use converted content/type/filename)
        blob_url = self._upload_to_blob(
            file_content=file_content,
            file_name=file_name,
            content_type=content_type,
        )
        
        # Create Attachment record
        logger.info(f"Creating new Attachment from QboAttachable {qbo_attachable.id}")
        attachment = self.attachment_service.create(
            filename=file_name,
            original_filename=qbo_attachable.file_name,
            file_extension=file_extension,
            content_type=content_type,
            file_size=len(file_content),
            file_hash=file_hash,
            blob_url=blob_url,
            description=qbo_attachable.note,
            category=qbo_attachable.category or "qbo_import",
            tags=None,
            is_archived=False,
            status="active",
            expiration_date=None,
            storage_tier="Hot",
        )
        
        # Create mapping
        self._create_mapping(attachment_id=attachment.id, qbo_attachable_id=qbo_attachable.id)
        logger.info(f"Created mapping: Attachment {attachment.id} <-> QboAttachable {qbo_attachable.id}")
        
        return attachment

    def _download_from_qbo(
        self,
        qbo_attachable: QboAttachable,
        realm_id: str,
    ) -> Optional[bytes]:
        """
        Download file content from QBO.
        
        Note: We fetch the attachable fresh from QBO to get a current TempDownloadUri,
        as the stored URI expires after a few minutes.
        
        Args:
            qbo_attachable: QboAttachable record
            realm_id: QBO realm ID
            
        Returns:
            File content as bytes, or None if download fails
        """
        # Get QBO auth
        qbo_auth = self.auth_service.ensure_valid_token(realm_id=realm_id)
        if not qbo_auth or not qbo_auth.access_token:
            logger.error(f"No valid QBO auth found for realm {realm_id}")
            return None

        if not qbo_attachable.qbo_id:
            logger.error(f"QboAttachable {qbo_attachable.id} has no qbo_id")
            return None

        with QboAttachableClient(
            access_token=qbo_auth.access_token,
            realm_id=realm_id
        ) as client:
            # Fetch fresh attachable from QBO to get a current TempDownloadUri
            # (the stored URI expires after a few minutes)
            try:
                fresh_attachable = client.get_attachable(qbo_attachable.qbo_id)
                logger.debug(f"Fetched fresh attachable {qbo_attachable.qbo_id} for download")
            except Exception as e:
                logger.error(f"Failed to fetch fresh attachable {qbo_attachable.qbo_id}: {e}")
                return None
            
            return client.download_attachable(fresh_attachable)

    def _upload_to_blob(
        self,
        file_content: bytes,
        file_name: str,
        content_type: str,
    ) -> str:
        """
        Upload file to Azure Blob Storage.
        
        Args:
            file_content: File content as bytes
            file_name: Original file name
            content_type: MIME content type
            
        Returns:
            Blob URL
        """
        # Generate unique blob name using public_id only (with extension)
        public_id = str(uuid.uuid4())
        # Extract extension from file_name
        import os
        _, ext = os.path.splitext(file_name)
        blob_name = f"{public_id}{ext}" if ext else public_id
        
        storage = AzureBlobStorage()
        blob_url = storage.upload_file(
            blob_name=blob_name,
            file_content=file_content,
            content_type=content_type,
        )
        
        logger.debug(f"Uploaded file to blob: {blob_url}")
        return blob_url

    def _create_mapping(self, attachment_id: int, qbo_attachable_id: int) -> AttachableAttachment:
        """
        Create a mapping between Attachment and QboAttachable.
        """
        # Validate 1:1 constraints
        existing_by_attachment = self.mapping_repo.read_by_attachment_id(attachment_id)
        if existing_by_attachment:
            raise ValueError(
                f"Attachment {attachment_id} is already mapped to QboAttachable {existing_by_attachment.qbo_attachable_id}"
            )
        
        existing_by_qbo = self.mapping_repo.read_by_qbo_attachable_id(qbo_attachable_id)
        if existing_by_qbo:
            raise ValueError(
                f"QboAttachable {qbo_attachable_id} is already mapped to Attachment {existing_by_qbo.attachment_id}"
            )
        
        return self.mapping_repo.create(attachment_id=attachment_id, qbo_attachable_id=qbo_attachable_id)

    def _ensure_pdf_filename(self, file_name: str) -> str:
        """Ensure filename has .pdf extension (e.g. after image-to-PDF conversion)."""
        if not file_name:
            return "attachment.pdf"
        base, ext = os.path.splitext(file_name)
        return f"{base}.pdf" if base else "attachment.pdf"

    def get_mapping_by_attachment_id(self, attachment_id: int) -> Optional[AttachableAttachment]:
        """
        Get mapping by Attachment ID.
        """
        return self.mapping_repo.read_by_attachment_id(attachment_id)

    def get_mapping_by_qbo_attachable_id(self, qbo_attachable_id: int) -> Optional[AttachableAttachment]:
        """
        Get mapping by QboAttachable ID.
        """
        return self.mapping_repo.read_by_qbo_attachable_id(qbo_attachable_id)

    def sync_attachment_to_qbo(
        self,
        attachment: Attachment,
        realm_id: str,
        entity_type: str,
        entity_id: str,
    ) -> QboAttachable:
        """
        Sync a local Attachment to QuickBooks Online.
        
        This method:
        1. Checks if a mapping already exists (skip if already synced)
        2. Downloads the file from Azure Blob Storage
        3. Uploads to QBO via the upload endpoint
        4. Stores QboAttachable locally and creates mapping
        
        Args:
            attachment: Local Attachment record to sync
            realm_id: QBO realm ID for API access
            entity_type: QBO entity type to link to (e.g., "Bill")
            entity_id: QBO entity ID to link to
        
        Returns:
            QboAttachable: The local QboAttachable record created
            
        Raises:
            ValueError: If upload fails or file cannot be downloaded
        """
        attachment_id = int(attachment.id) if isinstance(attachment.id, str) else attachment.id
        
        # Check if already mapped
        existing_mapping = self.mapping_repo.read_by_attachment_id(attachment_id)
        if existing_mapping:
            logger.info(f"Attachment {attachment_id} is already mapped to QboAttachable {existing_mapping.qbo_attachable_id}")
            qbo_attachable_repo = QboAttachableRepository()
            return qbo_attachable_repo.read_by_id(existing_mapping.qbo_attachable_id)
        
        # Download file from Azure Blob Storage
        if not attachment.blob_url:
            raise ValueError(f"Attachment {attachment_id} has no blob_url")
        
        try:
            storage = AzureBlobStorage()
            file_content, metadata = storage.download_file(attachment.blob_url)
            logger.debug(f"Downloaded attachment {attachment_id} from blob: {len(file_content)} bytes")
        except AzureBlobStorageError as e:
            raise ValueError(f"Failed to download attachment from blob storage: {e}")
        
        # Get QBO auth
        qbo_auth = self.auth_service.ensure_valid_token(realm_id=realm_id)
        if not qbo_auth or not qbo_auth.access_token:
            raise ValueError(f"No valid QBO auth found for realm {realm_id}")
        
        # Upload to QBO
        filename = attachment.original_filename or attachment.filename or f"attachment_{attachment.public_id}"
        content_type = attachment.content_type or metadata.get("content_type", "application/octet-stream")
        
        logger.info(f"Uploading attachment {attachment_id} to QBO: {filename} -> {entity_type} {entity_id}")
        
        with QboAttachableClient(
            access_token=qbo_auth.access_token,
            realm_id=realm_id
        ) as client:
            qbo_attachable_response = client.upload_attachable(
                file_content=file_content,
                filename=filename,
                content_type=content_type,
                entity_type=entity_type,
                entity_id=entity_id,
                note=attachment.description,
            )
        
        logger.info(f"Created QBO Attachable {qbo_attachable_response.id} for {entity_type} {entity_id}")
        
        # Store QboAttachable locally
        qbo_attachable_repo = QboAttachableRepository()
        local_qbo_attachable = qbo_attachable_repo.create(
            qbo_id=qbo_attachable_response.id,
            sync_token=qbo_attachable_response.sync_token,
            realm_id=realm_id,
            file_name=qbo_attachable_response.file_name,
            note=qbo_attachable_response.note,
            category=qbo_attachable_response.category,
            content_type=qbo_attachable_response.content_type,
            size=qbo_attachable_response.size,
            file_access_uri=qbo_attachable_response.file_access_uri,
            temp_download_uri=qbo_attachable_response.temp_download_uri,
            entity_ref_type=entity_type,
            entity_ref_value=entity_id,
        )
        
        logger.info(f"Stored local QboAttachable {local_qbo_attachable.id}")
        
        # Create mapping
        qbo_attachable_id = int(local_qbo_attachable.id) if isinstance(local_qbo_attachable.id, str) else local_qbo_attachable.id
        try:
            self._create_mapping(attachment_id=attachment_id, qbo_attachable_id=qbo_attachable_id)
            logger.info(f"Created mapping: Attachment {attachment_id} <-> QboAttachable {qbo_attachable_id}")
        except ValueError as e:
            logger.warning(f"Could not create mapping: {e}")
        
        return local_qbo_attachable
