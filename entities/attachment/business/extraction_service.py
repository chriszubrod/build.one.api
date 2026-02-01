# Python Standard Library Imports
import json
import logging
from dataclasses import asdict
from typing import Optional, List, Any

# Third-party Imports

# Local Imports
from entities.attachment.business.model import Attachment
from entities.attachment.persistence.repo import AttachmentRepository
from integrations.azure.ai import AzureDocumentIntelligence
from integrations.azure.ai.document_intelligence import AzureDocumentIntelligenceError, ExtractionResult
from shared.storage import AzureBlobStorage, AzureBlobStorageError

logger = logging.getLogger(__name__)


# Content types that support text extraction
EXTRACTABLE_CONTENT_TYPES = {
    # PDF
    "application/pdf",
    # Images
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/tiff",
    "image/bmp",
    "image/gif",
    # Office documents (converted to PDF by Document Intelligence)
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # docx
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # xlsx
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # pptx
    "application/msword",  # doc
    "application/vnd.ms-excel",  # xls
    "application/vnd.ms-powerpoint",  # ppt
}


class ExtractionService:
    """
    Service for extracting text from attachments using Azure Document Intelligence.
    
    Extracted text is stored as JSON in Azure Blob Storage (not in SQL).
    This keeps the database lean and leverages cheap blob storage for large text.
    """

    EXTRACTION_CONTAINER = "extractions"  # Blob container for extraction results

    def __init__(
        self,
        repo: Optional[AttachmentRepository] = None,
        doc_intelligence: Optional[AzureDocumentIntelligence] = None,
        blob_storage: Optional[AzureBlobStorage] = None,
    ):
        """Initialize the ExtractionService."""
        self.repo = repo or AttachmentRepository()
        self._doc_intelligence = doc_intelligence
        self._blob_storage = blob_storage

    @property
    def doc_intelligence(self) -> AzureDocumentIntelligence:
        """Lazy load Document Intelligence client."""
        if self._doc_intelligence is None:
            self._doc_intelligence = AzureDocumentIntelligence()
        return self._doc_intelligence

    @property
    def blob_storage(self) -> AzureBlobStorage:
        """Lazy load Blob Storage client."""
        if self._blob_storage is None:
            self._blob_storage = AzureBlobStorage()
        return self._blob_storage

    def is_extractable(self, attachment: Attachment) -> bool:
        """
        Check if an attachment's content type supports text extraction.

        Args:
            attachment: The attachment to check.

        Returns:
            True if the content type is extractable.
        """
        if not attachment.content_type:
            return False
        return attachment.content_type.lower() in EXTRACTABLE_CONTENT_TYPES

    def should_extract(self, attachment: Attachment) -> bool:
        """
        Check if an attachment should be extracted.

        Returns True if:
        - Content type is extractable
        - Extraction status is None or 'pending'
        - Has a blob_url

        Args:
            attachment: The attachment to check.

        Returns:
            True if extraction should proceed.
        """
        if not self.is_extractable(attachment):
            return False

        if not attachment.blob_url:
            return False

        # Only extract if not already done
        if attachment.extraction_status in ('processing', 'completed'):
            return False

        return True

    def _extraction_result_to_dict(self, result: ExtractionResult) -> dict[str, Any]:
        """
        Convert ExtractionResult to a JSON-serializable dictionary.

        Args:
            result: The ExtractionResult from Document Intelligence.

        Returns:
            Dictionary ready for JSON serialization.
        """
        return {
            "content": result.content,
            "pages": result.pages,
            "tables": [asdict(t) for t in result.tables],
            "paragraphs": result.paragraphs,
            "key_value_pairs": result.key_value_pairs,
        }

    def _save_extraction_to_blob(self, public_id: str, result: ExtractionResult) -> str:
        """
        Save extraction results to blob storage as JSON.

        Args:
            public_id: The attachment public ID / UUID (used for blob naming).
            result: The ExtractionResult to save.

        Returns:
            The blob URL where the extraction was saved.
        """
        # Create JSON content
        extraction_data = self._extraction_result_to_dict(result)
        json_content = json.dumps(extraction_data, ensure_ascii=False, indent=2)
        
        # Upload to blob storage in extractions container
        # Naming convention: {uuid}_extraction.json (matches {uuid}.pdf for original files)
        blob_name = f"{public_id}_extraction.json"
        blob_url = self.blob_storage.upload_file(
            blob_name=blob_name,
            file_content=json_content.encode("utf-8"),
            content_type="application/json",
            container_name=self.EXTRACTION_CONTAINER,
        )
        
        return blob_url

    def get_extraction_result(self, attachment: Attachment) -> Optional[dict[str, Any]]:
        """
        Retrieve the extraction result from blob storage.

        Args:
            attachment: The attachment to get extraction for.

        Returns:
            The extraction result as a dictionary, or None if not available.
        """
        if not attachment.extracted_text_blob_url:
            return None

        try:
            file_content, _ = self.blob_storage.download_file(attachment.extracted_text_blob_url)
            return json.loads(file_content.decode("utf-8"))
        except AzureBlobStorageError as e:
            logger.error(f"Failed to download extraction for attachment {attachment.id}: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse extraction JSON for attachment {attachment.id}: {e}")
            return None

    def get_extracted_text(self, attachment: Attachment) -> Optional[str]:
        """
        Get just the extracted text content (not full result).

        Args:
            attachment: The attachment to get text for.

        Returns:
            The extracted text content, or None if not available.
        """
        result = self.get_extraction_result(attachment)
        if result:
            return result.get("content")
        return None

    def extract_attachment(self, attachment: Attachment, index_after: bool = True) -> Attachment:
        """
        Extract text from an attachment.

        This method:
        1. Sets status to 'processing'
        2. Downloads the file from blob storage
        3. Extracts text using Document Intelligence
        4. Saves extraction result as JSON to blob storage
        5. Updates the attachment with blob URL
        6. Sets status to 'completed' or 'failed'
        7. Optionally indexes in Azure AI Search

        Args:
            attachment: The attachment to extract.
            index_after: If True, index the document after successful extraction.

        Returns:
            Updated Attachment with extraction results.
        """
        if not attachment.id:
            raise ValueError("Attachment must have an ID")

        # Mark as processing
        self.repo.update_extraction(
            id=attachment.id,
            extraction_status="processing",
        )

        try:
            # Check if extractable
            if not self.is_extractable(attachment):
                logger.info(f"Attachment {attachment.id} content type not extractable: {attachment.content_type}")
                return self.repo.update_extraction(
                    id=attachment.id,
                    extraction_status="completed",
                    extracted_text_blob_url=None,
                    extraction_error="Content type not supported for extraction",
                )

            # Download from blob storage
            logger.info(f"Downloading attachment {attachment.id} from blob storage")
            try:
                file_content, metadata = self.blob_storage.download_file(attachment.blob_url)
            except AzureBlobStorageError as e:
                logger.error(f"Failed to download attachment {attachment.id}: {e}")
                return self.repo.update_extraction(
                    id=attachment.id,
                    extraction_status="failed",
                    extraction_error=f"Failed to download file: {str(e)}",
                )

            # Extract text using Document Intelligence
            logger.info(f"Extracting text from attachment {attachment.id}")
            try:
                result = self.doc_intelligence.extract_document(
                    file_content=file_content,
                    content_type=attachment.content_type or "application/octet-stream",
                )

                # Log extraction stats
                logger.info(
                    f"Extracted from attachment {attachment.id}: "
                    f"{len(result.content)} chars, "
                    f"{len(result.pages)} pages, "
                    f"{len(result.tables)} tables"
                )

                # Save extraction result to blob storage
                logger.info(f"Saving extraction result to blob for attachment {attachment.id}")
                extraction_blob_url = self._save_extraction_to_blob(attachment.public_id, result)

                # Update with success
                updated = self.repo.update_extraction(
                    id=attachment.id,
                    extraction_status="completed",
                    extracted_text_blob_url=extraction_blob_url,
                )

                # Index in Azure AI Search if requested
                if index_after and updated:
                    try:
                        from entities.search.business.service import get_search_service
                        search_service = get_search_service()
                        search_service.index_attachment(updated)
                        logger.info(f"Indexed attachment {attachment.id} in Azure AI Search")
                    except Exception as e:
                        # Log but don't fail - indexing is non-critical
                        logger.error(f"Failed to index attachment {attachment.id}: {e}")

                return updated

            except AzureDocumentIntelligenceError as e:
                logger.error(f"Document Intelligence error for attachment {attachment.id}: {e}")
                return self.repo.update_extraction(
                    id=attachment.id,
                    extraction_status="failed",
                    extraction_error=f"Extraction failed: {str(e)}",
                )

        except Exception as e:
            logger.exception(f"Unexpected error extracting attachment {attachment.id}")
            return self.repo.update_extraction(
                id=attachment.id,
                extraction_status="failed",
                extraction_error=f"Unexpected error: {str(e)}",
            )

    def extract_attachment_by_id(self, attachment_id: int) -> Optional[Attachment]:
        """
        Extract text from an attachment by ID.

        Args:
            attachment_id: The attachment ID.

        Returns:
            Updated Attachment or None if not found.
        """
        attachment = self.repo.read_by_id(attachment_id)
        if not attachment:
            logger.warning(f"Attachment {attachment_id} not found for extraction")
            return None

        return self.extract_attachment(attachment)

    def process_pending_extractions(self, limit: int = 10) -> List[Attachment]:
        """
        Process pending extractions in batch.

        Args:
            limit: Maximum number of attachments to process.

        Returns:
            List of processed Attachments.
        """
        pending = self.repo.read_pending_extraction()
        logger.info(f"Found {len(pending)} attachments pending extraction")

        # Filter to only extractable ones and apply limit
        to_process = [a for a in pending if self.should_extract(a)][:limit]
        logger.info(f"Processing {len(to_process)} extractable attachments")

        results = []
        for attachment in to_process:
            try:
                result = self.extract_attachment(attachment)
                results.append(result)
            except Exception as e:
                logger.error(f"Error processing attachment {attachment.id}: {e}")
                # Continue with next attachment

        return results

    def mark_as_pending(self, attachment_id: int) -> Optional[Attachment]:
        """
        Mark an attachment as pending extraction.

        Useful for re-triggering extraction on an attachment.

        Args:
            attachment_id: The attachment ID.

        Returns:
            Updated Attachment or None if not found.
        """
        return self.repo.update_extraction(
            id=attachment_id,
            extraction_status="pending",
            extracted_text_blob_url=None,
            extraction_error=None,
        )

    def delete_extraction(self, attachment: Attachment) -> bool:
        """
        Delete the extraction blob for an attachment.

        Args:
            attachment: The attachment whose extraction to delete.

        Returns:
            True if deleted successfully, False otherwise.
        """
        if not attachment.extracted_text_blob_url:
            return True  # Nothing to delete

        try:
            self.blob_storage.delete_file(attachment.extracted_text_blob_url)
            return True
        except AzureBlobStorageError as e:
            logger.error(f"Failed to delete extraction blob for attachment {attachment.id}: {e}")
            return False
