# Python Standard Library Imports
import logging
from typing import List

# Third-party Imports
import httpx

# Local Imports
import config

logger = logging.getLogger(__name__)

# text-embedding-ada-002 and text-embedding-3-small produce 1536 dimensions
AZURE_EMBEDDING_DIMENSION = 1536


class AzureEmbeddingError(Exception):
    """Base exception for Azure embedding operations."""
    pass


class AzureEmbeddingService:
    """
    Embedding generation using Azure OpenAI.
    Use in production to avoid loading torch/sentence-transformers.
    """

    def __init__(self):
        """Initialize Azure embedding service."""
        settings = config.Settings()
        self.endpoint = (settings.azure_openai_endpoint or "").rstrip("/")
        self.api_key = settings.azure_openai_api_key
        self.deployment_name = settings.azure_embedding_deployment_name
        self.api_version = settings.azure_openai_api_version
        self._dimension = AZURE_EMBEDDING_DIMENSION

        if not self.endpoint or not self.api_key:
            raise ValueError("Azure OpenAI endpoint and api_key required for Azure embeddings")

        logger.info(f"AzureEmbeddingService initialized with deployment {self.deployment_name}")

    def _get_headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "api-key": self.api_key,
        }

    def _build_url(self) -> str:
        return (
            f"{self.endpoint}/openai/deployments/{self.deployment_name}"
            f"/embeddings?api-version={self.api_version}"
        )

    def _call_embeddings_api(self, inputs: List[str]) -> List[List[float]]:
        """Call Azure embeddings API. Inputs can be 1 or more texts."""
        url = self._build_url()
        headers = self._get_headers()
        payload = {"input": inputs if len(inputs) > 1 else inputs[0]}

        with httpx.Client(timeout=60.0) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

        # Response format: {"data": [{"embedding": [...], "index": 0}, ...]}
        items = data.get("data", [])
        if len(items) == 1 and isinstance(payload["input"], str):
            return [items[0]["embedding"]]
        return [item["embedding"] for item in sorted(items, key=lambda x: x.get("index", 0))]

    @property
    def dimension(self) -> int:
        return self._dimension

    def generate_embedding(self, text: str) -> List[float]:
        if not text or not text.strip():
            return [0.0] * self._dimension
        return self._call_embeddings_api([text])[0]

    def generate_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        processed = []
        empty_indices = set()
        for i, t in enumerate(texts):
            if not t or not t.strip():
                empty_indices.add(i)
                processed.append("placeholder")
            else:
                processed.append(t)
        result = self._call_embeddings_api(processed)
        for i in empty_indices:
            result[i] = [0.0] * self._dimension
        return result

    def compute_similarity(self, embedding1: List[float], embedding2: List[float]) -> float:
        if len(embedding1) != len(embedding2):
            raise ValueError("Embeddings must have the same dimension")
        dot = sum(a * b for a, b in zip(embedding1, embedding2))
        n1 = sum(a * a for a in embedding1) ** 0.5
        n2 = sum(b * b for b in embedding2) ** 0.5
        if n1 == 0 or n2 == 0:
            return 0.0
        return dot / (n1 * n2)

    def find_most_similar(
        self,
        query_embedding: List[float],
        candidate_embeddings: List[List[float]],
        top_k: int = 5,
    ) -> List[tuple]:
        if not candidate_embeddings:
            return []
        sims = [
            (i, self.compute_similarity(query_embedding, c))
            for i, c in enumerate(candidate_embeddings)
        ]
        sims.sort(key=lambda x: x[1], reverse=True)
        return sims[:top_k]
