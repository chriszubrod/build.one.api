# Python Standard Library Imports
import logging
from typing import List, Optional

# Local Imports
import config

logger = logging.getLogger(__name__)

# Module-level singleton for lazy loading
_embedding_service = None


def get_embedding_service():
    """
    Get the singleton embedding service instance.

    Uses Azure OpenAI embeddings (requires azure_openai_endpoint and
    azure_openai_api_key in app settings).

    Returns:
        AzureEmbeddingService instance.

    Raises:
        RuntimeError: If Azure OpenAI is not configured.
    """
    global _embedding_service

    if _embedding_service is None:
        settings = config.Settings()
        if not settings.azure_openai_endpoint or not settings.azure_openai_api_key:
            raise RuntimeError(
                "Azure OpenAI embeddings required but not configured. "
                "Set AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY."
            )
        from integrations.azure.ai.embeddings import AzureEmbeddingService
        _embedding_service = AzureEmbeddingService()

    return _embedding_service


def compute_similarity(embedding1: List[float], embedding2: List[float]) -> float:
    """Compute cosine similarity between two embeddings."""
    if len(embedding1) != len(embedding2):
        raise ValueError("Embeddings must have the same dimension")

    dot_product = sum(a * b for a, b in zip(embedding1, embedding2))
    norm1 = sum(a * a for a in embedding1) ** 0.5
    norm2 = sum(b * b for b in embedding2) ** 0.5

    if norm1 == 0 or norm2 == 0:
        return 0.0

    return dot_product / (norm1 * norm2)
