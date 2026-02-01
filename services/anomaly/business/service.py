# Python Standard Library Imports
import logging
from typing import Optional, List, Dict, Any

# Third-party Imports

# Local Imports
from services.anomaly.business.model import (
    AnomalyResult,
    AnomalyType,
    AnomalySeverity,
    RelatedDocument,
)
from services.attachment.business.model import Attachment
from services.attachment.business.service import AttachmentService
from services.attachment.business.extraction_service import ExtractionService
from integrations.azure.ai import AzureSearchClient
from shared.ai import get_embedding_service

logger = logging.getLogger(__name__)


class AnomalyDetectionConfig:
    """
    Configuration for anomaly detection thresholds.
    
    These can be adjusted based on business needs.
    """
    # Semantic similarity thresholds
    NEAR_DUPLICATE_THRESHOLD = 0.95   # 95%+ similarity = near duplicate
    SIMILAR_CONTENT_THRESHOLD = 0.85  # 85%+ similarity = similar content
    
    # Whether to check each type
    CHECK_EXACT_DUPLICATES = True
    CHECK_NEAR_DUPLICATES = True
    CHECK_SIMILAR_CONTENT = True


class AnomalyDetectionService:
    """
    Service for detecting anomalies in attachments.
    
    Currently supports:
    - Exact duplicate detection (file hash)
    - Near duplicate detection (semantic similarity)
    - Similar content detection (informational)
    
    Future extensions:
    - Amount anomalies (for bills)
    - Timing anomalies
    - Pattern detection
    """

    def __init__(
        self,
        attachment_service: Optional[AttachmentService] = None,
        extraction_service: Optional[ExtractionService] = None,
        search_client: Optional[AzureSearchClient] = None,
        config: Optional[AnomalyDetectionConfig] = None,
    ):
        """Initialize the AnomalyDetectionService."""
        self._attachment_service = attachment_service
        self._extraction_service = extraction_service
        self._search_client = search_client
        self._embedding_service = None
        self.config = config or AnomalyDetectionConfig()

    @property
    def attachment_service(self) -> AttachmentService:
        """Lazy load attachment service."""
        if self._attachment_service is None:
            self._attachment_service = AttachmentService()
        return self._attachment_service

    @property
    def extraction_service(self) -> ExtractionService:
        """Lazy load extraction service."""
        if self._extraction_service is None:
            self._extraction_service = ExtractionService()
        return self._extraction_service

    @property
    def search_client(self) -> AzureSearchClient:
        """Lazy load search client."""
        if self._search_client is None:
            self._search_client = AzureSearchClient()
        return self._search_client

    @property
    def embedding_service(self):
        """Lazy load embedding service."""
        if self._embedding_service is None:
            self._embedding_service = get_embedding_service()
        return self._embedding_service

    def check_attachment(
        self,
        attachment: Attachment,
        check_category_only: bool = True,
    ) -> AnomalyResult:
        """
        Check an attachment for anomalies.

        Args:
            attachment: The attachment to check.
            check_category_only: If True, only compare against documents
                                 in the same category.

        Returns:
            AnomalyResult with detection results.
        """
        logger.info(f"Checking attachment {attachment.id} for anomalies")

        # Step 1: Check for exact duplicates (by file hash)
        if self.config.CHECK_EXACT_DUPLICATES and attachment.file_hash:
            exact_result = self._check_exact_duplicate(attachment)
            if exact_result.has_anomaly:
                logger.info(f"Exact duplicate found for attachment {attachment.id}")
                return exact_result

        # Step 2: Check for near duplicates (by semantic similarity)
        if self.config.CHECK_NEAR_DUPLICATES or self.config.CHECK_SIMILAR_CONTENT:
            similarity_result = self._check_semantic_similarity(
                attachment,
                check_category_only=check_category_only,
            )
            if similarity_result.has_anomaly:
                logger.info(
                    f"Similarity anomaly ({similarity_result.anomaly_type}) "
                    f"found for attachment {attachment.id}"
                )
                return similarity_result

        logger.info(f"No anomalies detected for attachment {attachment.id}")
        return AnomalyResult.no_anomaly()

    def check_attachment_by_id(
        self,
        attachment_id: int,
        check_category_only: bool = True,
    ) -> Optional[AnomalyResult]:
        """
        Check an attachment for anomalies by ID.

        Args:
            attachment_id: The attachment ID.
            check_category_only: If True, only compare against same category.

        Returns:
            AnomalyResult or None if attachment not found.
        """
        attachment = self.attachment_service.read_by_id(attachment_id)
        if not attachment:
            logger.warning(f"Attachment {attachment_id} not found")
            return None
        return self.check_attachment(attachment, check_category_only)

    def check_attachment_by_public_id(
        self,
        public_id: str,
        check_category_only: bool = True,
    ) -> Optional[AnomalyResult]:
        """
        Check an attachment for anomalies by public ID.

        Args:
            public_id: The attachment public ID.
            check_category_only: If True, only compare against same category.

        Returns:
            AnomalyResult or None if attachment not found.
        """
        attachment = self.attachment_service.read_by_public_id(public_id)
        if not attachment:
            logger.warning(f"Attachment {public_id} not found")
            return None
        return self.check_attachment(attachment, check_category_only)

    def _check_exact_duplicate(self, attachment: Attachment) -> AnomalyResult:
        """
        Check for exact duplicates using file hash.

        Args:
            attachment: The attachment to check.

        Returns:
            AnomalyResult indicating if exact duplicate found.
        """
        if not attachment.file_hash:
            return AnomalyResult.no_anomaly()

        # Look for other attachments with the same hash
        existing = self.attachment_service.read_by_hash(attachment.file_hash)

        if existing and existing.id != attachment.id:
            # Found a duplicate
            related = [
                RelatedDocument(
                    public_id=str(existing.public_id),
                    filename=existing.original_filename or existing.filename,
                    category=existing.category,
                    similarity_score=1.0,
                    match_reason="Identical file hash",
                )
            ]
            return AnomalyResult.exact_duplicate(related)

        return AnomalyResult.no_anomaly()

    def _check_semantic_similarity(
        self,
        attachment: Attachment,
        check_category_only: bool = True,
    ) -> AnomalyResult:
        """
        Check for near duplicates using semantic similarity.

        Args:
            attachment: The attachment to check.
            check_category_only: If True, only compare against same category.

        Returns:
            AnomalyResult indicating similarity level.
        """
        # Need extracted text for semantic comparison
        if attachment.extraction_status != "completed":
            logger.debug(f"Attachment {attachment.id} not extracted, skipping similarity check")
            return AnomalyResult.no_anomaly()

        # Get the extracted text
        extracted_text = self.extraction_service.get_extracted_text(attachment)
        if not extracted_text:
            return AnomalyResult.no_anomaly()

        # Generate embedding for this document
        query_vector = self.embedding_service.generate_embedding(extracted_text)

        # Search for similar documents
        try:
            filter_expression = None
            if check_category_only and attachment.category:
                filter_expression = f"category eq '{attachment.category}'"

            results = self.search_client.vector_search(
                vector=query_vector,
                vector_field="content_vector",
                k=10,  # Get top 10 similar
                filter_expression=filter_expression,
                select=["id", "attachment_id", "filename", "original_filename", "category"],
            )
        except Exception as e:
            logger.error(f"Error searching for similar documents: {e}")
            return AnomalyResult.no_anomaly()

        # Filter out the current document and analyze results
        related_docs = []
        max_similarity = 0.0

        for doc in results.get("value", []):
            doc_public_id = doc.get("id")
            doc_attachment_id = doc.get("attachment_id")

            # Skip the current document
            if str(attachment.public_id) == doc_public_id:
                continue
            if attachment.id == doc_attachment_id:
                continue

            # Get similarity score (Azure Search returns @search.score)
            # For vector search, higher score = more similar
            similarity = doc.get("@search.score", 0)

            if similarity > max_similarity:
                max_similarity = similarity

            # Only include if above minimum threshold
            if similarity >= self.config.SIMILAR_CONTENT_THRESHOLD:
                related_docs.append(
                    RelatedDocument(
                        public_id=doc_public_id,
                        filename=doc.get("original_filename") or doc.get("filename"),
                        category=doc.get("category"),
                        similarity_score=similarity,
                        match_reason=f"{similarity:.0%} semantic similarity",
                    )
                )

        if not related_docs:
            return AnomalyResult.no_anomaly()

        # Determine anomaly type based on similarity level
        if max_similarity >= self.config.NEAR_DUPLICATE_THRESHOLD:
            return AnomalyResult.near_duplicate(related_docs, max_similarity)
        elif max_similarity >= self.config.SIMILAR_CONTENT_THRESHOLD:
            return AnomalyResult.similar_content(related_docs, max_similarity)
        else:
            return AnomalyResult.no_anomaly()

    def check_for_duplicates_before_upload(
        self,
        file_hash: str,
        extracted_text: Optional[str] = None,
        category: Optional[str] = None,
    ) -> AnomalyResult:
        """
        Check for duplicates before uploading a new attachment.
        
        Useful for pre-upload validation.

        Args:
            file_hash: The hash of the file to check.
            extracted_text: Optional extracted text for semantic check.
            category: Optional category to filter by.

        Returns:
            AnomalyResult with any detected duplicates.
        """
        # Check exact duplicate by hash
        existing = self.attachment_service.read_by_hash(file_hash)
        if existing:
            related = [
                RelatedDocument(
                    public_id=str(existing.public_id),
                    filename=existing.original_filename or existing.filename,
                    category=existing.category,
                    similarity_score=1.0,
                    match_reason="Identical file hash",
                )
            ]
            return AnomalyResult.exact_duplicate(related)

        # If text provided, check semantic similarity
        if extracted_text:
            query_vector = self.embedding_service.generate_embedding(extracted_text)

            filter_expression = None
            if category:
                filter_expression = f"category eq '{category}'"

            try:
                results = self.search_client.vector_search(
                    vector=query_vector,
                    vector_field="content_vector",
                    k=5,
                    filter_expression=filter_expression,
                    select=["id", "filename", "original_filename", "category"],
                )

                related_docs = []
                max_similarity = 0.0

                for doc in results.get("value", []):
                    similarity = doc.get("@search.score", 0)
                    if similarity > max_similarity:
                        max_similarity = similarity

                    if similarity >= self.config.SIMILAR_CONTENT_THRESHOLD:
                        related_docs.append(
                            RelatedDocument(
                                public_id=doc.get("id"),
                                filename=doc.get("original_filename") or doc.get("filename"),
                                category=doc.get("category"),
                                similarity_score=similarity,
                                match_reason=f"{similarity:.0%} semantic similarity",
                            )
                        )

                if related_docs and max_similarity >= self.config.NEAR_DUPLICATE_THRESHOLD:
                    return AnomalyResult.near_duplicate(related_docs, max_similarity)

            except Exception as e:
                logger.error(f"Error in pre-upload similarity check: {e}")

        return AnomalyResult.no_anomaly()


# Singleton instance
_anomaly_service: Optional[AnomalyDetectionService] = None


def get_anomaly_service() -> AnomalyDetectionService:
    """Get or create the singleton AnomalyDetectionService instance."""
    global _anomaly_service
    if _anomaly_service is None:
        _anomaly_service = AnomalyDetectionService()
    return _anomaly_service
