# Python Standard Library Imports
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

# Third-party Imports

# Local Imports
from services.attachment.business.model import Attachment
from services.attachment.business.extraction_service import ExtractionService
from integrations.azure.ai import AzureSearchClient
from integrations.azure.ai.search_client import AzureSearchError
from shared.ai import get_embedding_service

logger = logging.getLogger(__name__)


class SearchService:
    """
    Service for indexing and searching attachments using Azure AI Search.
    
    Combines:
    - Local embeddings (all-MiniLM-L6-v2) for vector generation
    - Azure AI Search for hybrid keyword + semantic search
    """

    def __init__(
        self,
        search_client: Optional[AzureSearchClient] = None,
        extraction_service: Optional[ExtractionService] = None,
    ):
        """Initialize the SearchService."""
        self._search_client = search_client
        self._extraction_service = extraction_service
        self._embedding_service = None

    @property
    def search_client(self) -> AzureSearchClient:
        """Lazy load search client."""
        if self._search_client is None:
            self._search_client = AzureSearchClient()
        return self._search_client

    @property
    def extraction_service(self) -> ExtractionService:
        """Lazy load extraction service."""
        if self._extraction_service is None:
            self._extraction_service = ExtractionService()
        return self._extraction_service

    @property
    def embedding_service(self):
        """Lazy load embedding service."""
        if self._embedding_service is None:
            self._embedding_service = get_embedding_service()
        return self._embedding_service

    def index_attachment(self, attachment: Attachment) -> bool:
        """
        Index an attachment in Azure AI Search.

        Retrieves extracted text, generates embeddings, and indexes the document.

        Args:
            attachment: The attachment to index (must have completed extraction).

        Returns:
            True if indexed successfully.

        Raises:
            ValueError: If attachment has no extracted text.
            AzureSearchError: If indexing fails.
        """
        if not attachment.public_id:
            raise ValueError("Attachment must have a public_id")

        if attachment.extraction_status != "completed":
            raise ValueError(f"Attachment extraction not completed: {attachment.extraction_status}")

        if not attachment.extracted_text_blob_url:
            raise ValueError("Attachment has no extracted text blob URL")

        # Get extracted text from blob storage
        logger.info(f"Retrieving extracted text for attachment {attachment.id}")
        extracted_text = self.extraction_service.get_extracted_text(attachment)

        if not extracted_text:
            logger.warning(f"No extracted text for attachment {attachment.id}")
            extracted_text = ""

        # Generate embeddings for the content
        logger.info(f"Generating embeddings for attachment {attachment.id}")
        content_vector = self.embedding_service.generate_embedding(extracted_text)

        # Create content preview (first 500 chars)
        content_preview = extracted_text[:500] if extracted_text else ""

        # Build document for indexing
        document = {
            "id": str(attachment.public_id),
            "attachment_id": attachment.id,
            "filename": attachment.filename,
            "original_filename": attachment.original_filename,
            "content_type": attachment.content_type,
            "category": attachment.category,
            "description": attachment.description,
            "content": extracted_text,
            "content_preview": content_preview,
            "content_vector": content_vector,
            "created_datetime": self._format_datetime(attachment.created_datetime),
            "extracted_datetime": self._format_datetime(attachment.extracted_datetime),
            "indexed_datetime": datetime.now(timezone.utc).isoformat(),
        }

        # Index the document
        logger.info(f"Indexing attachment {attachment.id} in Azure AI Search")
        try:
            result = self.search_client.upsert_documents([document])
            success = all(
                item.get("status", False) or item.get("statusCode") == 200
                for item in result.get("value", [])
            )
            if success:
                logger.info(f"Successfully indexed attachment {attachment.id}")
            else:
                logger.warning(f"Indexing result for attachment {attachment.id}: {result}")
            return success
        except AzureSearchError as e:
            logger.error(f"Failed to index attachment {attachment.id}: {e}")
            raise

    def remove_from_index(self, public_id: str) -> bool:
        """
        Remove an attachment from the search index.

        Args:
            public_id: The attachment public ID.

        Returns:
            True if removed successfully.
        """
        try:
            result = self.search_client.delete_documents("id", [str(public_id)])
            logger.info(f"Removed attachment {public_id} from search index")
            return True
        except AzureSearchError as e:
            logger.error(f"Failed to remove attachment {public_id} from index: {e}")
            return False

    def search(
        self,
        query: str,
        category: Optional[str] = None,
        content_type: Optional[str] = None,
        top: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Search attachments using keyword search.

        Args:
            query: Search query text.
            category: Optional category filter.
            content_type: Optional content type filter.
            top: Maximum number of results.

        Returns:
            List of matching documents with scores.
        """
        filter_parts = []
        if category:
            filter_parts.append(f"category eq '{self._escape_filter_value(category)}'")
        if content_type:
            filter_parts.append(f"content_type eq '{self._escape_filter_value(content_type)}'")

        filter_expression = " and ".join(filter_parts) if filter_parts else None

        result = self.search_client.search(
            search_text=query,
            filter_expression=filter_expression,
            select=["id", "attachment_id", "filename", "original_filename", 
                    "category", "content_preview", "created_datetime"],
            top=top,
        )

        return self._format_search_results(result)

    def semantic_search(
        self,
        query: str,
        category: Optional[str] = None,
        content_type: Optional[str] = None,
        top: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Search attachments using semantic (vector) search.

        Generates embeddings for the query and finds similar documents.

        Args:
            query: Search query text.
            category: Optional category filter.
            content_type: Optional content type filter.
            top: Maximum number of results.

        Returns:
            List of matching documents with similarity scores.
        """
        # Generate query embeddings
        query_vector = self.embedding_service.generate_embedding(query)

        filter_parts = []
        if category:
            filter_parts.append(f"category eq '{self._escape_filter_value(category)}'")
        if content_type:
            filter_parts.append(f"content_type eq '{self._escape_filter_value(content_type)}'")

        filter_expression = " and ".join(filter_parts) if filter_parts else None

        result = self.search_client.vector_search(
            vector=query_vector,
            vector_field="content_vector",
            k=top,
            filter_expression=filter_expression,
            select=["id", "attachment_id", "filename", "original_filename",
                    "category", "content_preview", "created_datetime"],
        )

        return self._format_search_results(result)

    def hybrid_search(
        self,
        query: str,
        category: Optional[str] = None,
        content_type: Optional[str] = None,
        top: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Search attachments using hybrid (keyword + vector) search.

        Combines keyword matching with semantic similarity for best results.

        Args:
            query: Search query text.
            category: Optional category filter.
            content_type: Optional content type filter.
            top: Maximum number of results.

        Returns:
            List of matching documents with combined scores.
        """
        # Generate query embeddings
        query_vector = self.embedding_service.generate_embedding(query)

        filter_parts = []
        if category:
            filter_parts.append(f"category eq '{self._escape_filter_value(category)}'")
        if content_type:
            filter_parts.append(f"content_type eq '{self._escape_filter_value(content_type)}'")

        filter_expression = " and ".join(filter_parts) if filter_parts else None

        result = self.search_client.hybrid_search(
            search_text=query,
            vector=query_vector,
            vector_field="content_vector",
            k=top,
            filter_expression=filter_expression,
            select=["id", "attachment_id", "filename", "original_filename",
                    "category", "content_preview", "created_datetime"],
        )

        return self._format_search_results(result)

    def _format_search_results(self, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Format raw search results into a cleaner structure."""
        documents = []
        for doc in result.get("value", []):
            documents.append({
                "public_id": doc.get("id"),
                "attachment_id": doc.get("attachment_id"),
                "filename": doc.get("filename"),
                "original_filename": doc.get("original_filename"),
                "category": doc.get("category"),
                "content_preview": doc.get("content_preview"),
                "created_datetime": doc.get("created_datetime"),
                "score": doc.get("@search.score"),
            })
        return documents

    @staticmethod
    def _escape_filter_value(value: str) -> str:
        """Escape string values for OData filter expressions."""
        return value.replace("'", "''")

    def _format_datetime(self, dt_str: Optional[str]) -> Optional[str]:
        """Convert datetime string to ISO format for Azure Search."""
        if not dt_str:
            return None
        try:
            # Handle various datetime formats
            if "T" in dt_str:
                return dt_str  # Already ISO format
            # Assume format from database: "YYYY-MM-DD HH:MM:SS"
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            return dt.replace(tzinfo=timezone.utc).isoformat()
        except Exception:
            return None


# Singleton instance
_search_service: Optional[SearchService] = None


def get_search_service() -> SearchService:
    """Get or create the singleton SearchService instance."""
    global _search_service
    if _search_service is None:
        _search_service = SearchService()
    return _search_service
