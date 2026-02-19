# Python Standard Library Imports
import logging
from typing import List, Optional, Union

# Local Imports
import config

logger = logging.getLogger(__name__)

# Module-level singleton for lazy loading
_embedding_service: Optional[Union["LocalEmbeddingService", "AzureEmbeddingService"]] = None


class LocalEmbeddingService:
    """
    Local embedding generation using Sentence Transformers.
    Runs on CPU - no GPU required.
    Used when Azure OpenAI is not configured (e.g. local dev).
    The model is loaded lazily on first use to avoid slowing down application startup.
    """

    def __init__(self, model_name: Optional[str] = None):
        """
        Initialize the embedding service.

        Args:
            model_name: Name of the Sentence Transformers model to use.
                        Defaults to configured model or 'all-MiniLM-L6-v2'.
        """
        settings = config.Settings()
        self.model_name = model_name or settings.local_embedding_model
        self._model = None
        self._dimension = None

        logger.info(f"LocalEmbeddingService initialized with model: {self.model_name}")

    def _load_model(self):
        """Load the model lazily on first use."""
        if self._model is None:
            logger.info(f"Loading embedding model: {self.model_name}")
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self.model_name)
                # Get embedding dimension from a test encoding
                test_embedding = self._model.encode("test", convert_to_numpy=True)
                self._dimension = len(test_embedding)
                logger.info(f"Model loaded successfully. Dimension: {self._dimension}")
            except ImportError:
                logger.error("sentence-transformers package not installed")
                raise ImportError(
                    "sentence-transformers is required for local embeddings. "
                    "Install with: pip install sentence-transformers"
                )
            except Exception as e:
                logger.error(f"Failed to load embedding model: {e}")
                raise

    @property
    def dimension(self) -> int:
        """Get the embedding dimension for this model."""
        if self._dimension is None:
            self._load_model()
        return self._dimension

    def generate_embedding(self, text: str) -> List[float]:
        """
        Generate an embedding vector for a single text.

        Args:
            text: The text to embed.

        Returns:
            List of floats representing the embedding vector.
        """
        self._load_model()

        if not text or not text.strip():
            logger.warning("Empty text provided for embedding")
            # Return zero vector for empty text
            return [0.0] * self._dimension

        embedding = self._model.encode(text, convert_to_numpy=True)
        return embedding.tolist()

    def generate_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embedding vectors for multiple texts.

        This is more efficient than calling generate_embedding() in a loop
        because it batches the computation.

        Args:
            texts: List of texts to embed.

        Returns:
            List of embedding vectors.
        """
        self._load_model()

        if not texts:
            return []

        # Handle empty strings by replacing with placeholder, then zero out
        processed_texts = []
        empty_indices = set()
        for i, text in enumerate(texts):
            if not text or not text.strip():
                empty_indices.add(i)
                processed_texts.append("placeholder")  # SentenceTransformer needs non-empty
            else:
                processed_texts.append(text)

        embeddings = self._model.encode(processed_texts, convert_to_numpy=True)
        result = embeddings.tolist()

        # Zero out embeddings for empty texts
        for i in empty_indices:
            result[i] = [0.0] * self._dimension

        return result

    def compute_similarity(self, embedding1: List[float], embedding2: List[float]) -> float:
        """
        Compute cosine similarity between two embeddings.

        Args:
            embedding1: First embedding vector.
            embedding2: Second embedding vector.

        Returns:
            Cosine similarity score between -1 and 1.
        """
        if len(embedding1) != len(embedding2):
            raise ValueError("Embeddings must have the same dimension")

        # Compute cosine similarity
        dot_product = sum(a * b for a, b in zip(embedding1, embedding2))
        norm1 = sum(a * a for a in embedding1) ** 0.5
        norm2 = sum(b * b for b in embedding2) ** 0.5

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return dot_product / (norm1 * norm2)

    def find_most_similar(
        self,
        query_embedding: List[float],
        candidate_embeddings: List[List[float]],
        top_k: int = 5,
    ) -> List[tuple]:
        """
        Find the most similar embeddings to a query.

        Args:
            query_embedding: The query embedding vector.
            candidate_embeddings: List of candidate embedding vectors.
            top_k: Number of top results to return.

        Returns:
            List of (index, similarity_score) tuples, sorted by similarity descending.
        """
        if not candidate_embeddings:
            return []

        similarities = [
            (i, self.compute_similarity(query_embedding, candidate))
            for i, candidate in enumerate(candidate_embeddings)
        ]

        # Sort by similarity descending
        similarities.sort(key=lambda x: x[1], reverse=True)

        return similarities[:top_k]


def get_embedding_service(model_name: Optional[str] = None):
    """
    Get the singleton embedding service instance.

    Uses Azure OpenAI embeddings when azure_openai_endpoint and azure_openai_api_key
    are configured (production). Otherwise uses local Sentence Transformers (dev).

    Args:
        model_name: Optional model name for local embeddings. Ignored when using Azure.

    Returns:
        Embedding service (Azure or Local) with generate_embedding, dimension, etc.
    """
    global _embedding_service

    if _embedding_service is None:
        settings = config.Settings()
        if settings.azure_openai_endpoint and settings.azure_openai_api_key:
            from integrations.azure.ai.embeddings import AzureEmbeddingService
            _embedding_service = AzureEmbeddingService()
        else:
            _embedding_service = LocalEmbeddingService(model_name)

    return _embedding_service


# Backward compatibility
EmbeddingService = LocalEmbeddingService
