# Local AI services module
from shared.ai.embeddings import (
    EmbeddingService,
    LocalEmbeddingService,
    get_embedding_service,
)

__all__ = [
    "EmbeddingService",
    "LocalEmbeddingService",
    "get_embedding_service",
]
