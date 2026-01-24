# Python Standard Library Imports
import logging
from typing import Optional, Dict, Any, List

# Third-party Imports
import httpx

# Local Imports
import config

logger = logging.getLogger(__name__)


class AzureSearchError(Exception):
    """Base exception for Azure AI Search operations."""
    pass


class AzureSearchClient:
    """
    Azure AI Search client using raw HTTP REST API.
    Supports index management, document operations, and vector search.
    """

    API_VERSION = "2024-07-01"

    def __init__(self, index_name: Optional[str] = None):
        """
        Initialize Azure AI Search client.

        Args:
            index_name: Optional index name override. If not provided,
                        uses the configured default.
        """
        settings = config.Settings()
        self.endpoint = settings.azure_search_endpoint
        self.api_key = settings.azure_search_api_key
        self.index_name = index_name or settings.azure_search_index_name

        if not self.endpoint:
            raise ValueError("Azure Search endpoint is required")
        if not self.api_key:
            raise ValueError("Azure Search API key is required")

        # Ensure endpoint doesn't have trailing slash
        self.endpoint = self.endpoint.rstrip("/")

    def _get_headers(self) -> dict:
        """Get standard headers for Azure Search requests."""
        return {
            "Content-Type": "application/json",
            "api-key": self.api_key,
        }

    def _build_url(self, path: str) -> str:
        """Build the full URL for an API endpoint."""
        return f"{self.endpoint}/{path}?api-version={self.API_VERSION}"

    # =========================================================================
    # Index Management
    # =========================================================================

    def create_index(
        self,
        fields: List[Dict[str, Any]],
        vector_search_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a search index.

        Args:
            fields: List of field definitions. Each field should have:
                - name: Field name
                - type: Field type (Edm.String, Edm.Int32, Collection(Edm.Single), etc.)
                - searchable: Whether field is full-text searchable
                - filterable: Whether field can be used in filters
                - sortable: Whether field can be used for sorting
                - key: Whether this is the document key field (only one)
                - dimensions: For vector fields, the vector dimensions
                - vectorSearchProfile: For vector fields, the profile name
            vector_search_config: Optional vector search configuration with
                algorithms and profiles for vector fields.

        Returns:
            Created index definition.

        Raises:
            AzureSearchError: If index creation fails.
        """
        try:
            url = self._build_url(f"indexes/{self.index_name}")
            headers = self._get_headers()

            payload = {
                "name": self.index_name,
                "fields": fields,
            }

            if vector_search_config:
                payload["vectorSearch"] = vector_search_config

            logger.debug(f"Creating search index: {self.index_name}")

            with httpx.Client(timeout=30.0) as client:
                response = client.put(url, headers=headers, json=payload)

                if response.status_code not in (200, 201):
                    error_text = response.text if hasattr(response, "text") else str(response.status_code)
                    logger.error(f"Index creation failed: {response.status_code} - {error_text}")
                    raise AzureSearchError(f"Index creation failed: {response.status_code}")

                return response.json()

        except AzureSearchError:
            raise
        except Exception as e:
            logger.error(f"Azure Search error: {e}")
            raise AzureSearchError(f"Index creation failed: {str(e)}")

    def delete_index(self) -> bool:
        """
        Delete the search index.

        Returns:
            True if deleted successfully.

        Raises:
            AzureSearchError: If deletion fails (except 404).
        """
        try:
            url = self._build_url(f"indexes/{self.index_name}")
            headers = self._get_headers()

            logger.debug(f"Deleting search index: {self.index_name}")

            with httpx.Client(timeout=30.0) as client:
                response = client.delete(url, headers=headers)

                if response.status_code == 404:
                    logger.info(f"Index {self.index_name} not found (already deleted)")
                    return True

                if response.status_code not in (204, 200):
                    error_text = response.text if hasattr(response, "text") else str(response.status_code)
                    logger.error(f"Index deletion failed: {response.status_code} - {error_text}")
                    raise AzureSearchError(f"Index deletion failed: {response.status_code}")

                return True

        except AzureSearchError:
            raise
        except Exception as e:
            logger.error(f"Azure Search error: {e}")
            raise AzureSearchError(f"Index deletion failed: {str(e)}")

    def get_index(self) -> Optional[Dict[str, Any]]:
        """
        Get the search index definition.

        Returns:
            Index definition or None if not found.

        Raises:
            AzureSearchError: If request fails (except 404).
        """
        try:
            url = self._build_url(f"indexes/{self.index_name}")
            headers = self._get_headers()

            with httpx.Client(timeout=30.0) as client:
                response = client.get(url, headers=headers)

                if response.status_code == 404:
                    return None

                response.raise_for_status()
                return response.json()

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            error_text = e.response.text if hasattr(e.response, "text") else str(e)
            logger.error(f"Get index failed: {e.response.status_code} - {error_text}")
            raise AzureSearchError(f"Get index failed: {e.response.status_code}")
        except Exception as e:
            logger.error(f"Azure Search error: {e}")
            raise AzureSearchError(f"Get index failed: {str(e)}")

    # =========================================================================
    # Document Operations
    # =========================================================================

    def upsert_documents(self, documents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Upload or merge documents into the index.

        Args:
            documents: List of documents to upsert. Each document must include
                       the key field. Use "@search.action" to specify action:
                       - "upload": Add or replace
                       - "merge": Update existing
                       - "mergeOrUpload": Update if exists, add if not (default)
                       - "delete": Remove document

        Returns:
            Result containing status for each document.

        Raises:
            AzureSearchError: If operation fails.
        """
        try:
            url = self._build_url(f"indexes/{self.index_name}/docs/index")
            headers = self._get_headers()

            # Set default action if not specified
            for doc in documents:
                if "@search.action" not in doc:
                    doc["@search.action"] = "mergeOrUpload"

            payload = {"value": documents}

            logger.debug(f"Upserting {len(documents)} documents to index {self.index_name}")

            with httpx.Client(timeout=60.0) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                return response.json()

        except httpx.HTTPStatusError as e:
            error_text = e.response.text if hasattr(e.response, "text") else str(e)
            logger.error(f"Document upsert failed: {e.response.status_code} - {error_text}")
            raise AzureSearchError(f"Document upsert failed: {e.response.status_code}")
        except Exception as e:
            logger.error(f"Azure Search error: {e}")
            raise AzureSearchError(f"Document upsert failed: {str(e)}")

    def delete_documents(self, key_field: str, keys: List[str]) -> Dict[str, Any]:
        """
        Delete documents by key.

        Args:
            key_field: Name of the key field in the index.
            keys: List of document keys to delete.

        Returns:
            Result containing status for each document.

        Raises:
            AzureSearchError: If operation fails.
        """
        documents = [
            {"@search.action": "delete", key_field: key}
            for key in keys
        ]
        return self.upsert_documents(documents)

    def get_document(self, key: str) -> Optional[Dict[str, Any]]:
        """
        Get a document by key.

        Args:
            key: Document key.

        Returns:
            Document or None if not found.

        Raises:
            AzureSearchError: If request fails (except 404).
        """
        try:
            url = self._build_url(f"indexes/{self.index_name}/docs/{key}")
            headers = self._get_headers()

            with httpx.Client(timeout=30.0) as client:
                response = client.get(url, headers=headers)

                if response.status_code == 404:
                    return None

                response.raise_for_status()
                return response.json()

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            error_text = e.response.text if hasattr(e.response, "text") else str(e)
            logger.error(f"Get document failed: {e.response.status_code} - {error_text}")
            raise AzureSearchError(f"Get document failed: {e.response.status_code}")
        except Exception as e:
            logger.error(f"Azure Search error: {e}")
            raise AzureSearchError(f"Get document failed: {str(e)}")

    # =========================================================================
    # Search Operations
    # =========================================================================

    def search(
        self,
        search_text: str = "*",
        filter_expression: Optional[str] = None,
        select: Optional[List[str]] = None,
        order_by: Optional[List[str]] = None,
        top: int = 50,
        skip: int = 0,
        include_total_count: bool = True,
    ) -> Dict[str, Any]:
        """
        Perform a keyword search.

        Args:
            search_text: Search query text. Use "*" for all documents.
            filter_expression: OData filter expression.
            select: List of fields to return.
            order_by: List of fields to sort by (use "field desc" for descending).
            top: Number of results to return.
            skip: Number of results to skip (for pagination).
            include_total_count: Whether to include total count.

        Returns:
            Search results with documents and metadata.

        Raises:
            AzureSearchError: If search fails.
        """
        try:
            url = self._build_url(f"indexes/{self.index_name}/docs/search")
            headers = self._get_headers()

            payload = {
                "search": search_text,
                "top": top,
                "skip": skip,
                "count": include_total_count,
            }

            if filter_expression:
                payload["filter"] = filter_expression
            if select:
                payload["select"] = ",".join(select)
            if order_by:
                payload["orderby"] = ",".join(order_by)

            logger.debug(f"Searching index {self.index_name}: {search_text}")

            with httpx.Client(timeout=30.0) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                return response.json()

        except httpx.HTTPStatusError as e:
            error_text = e.response.text if hasattr(e.response, "text") else str(e)
            logger.error(f"Search failed: {e.response.status_code} - {error_text}")
            raise AzureSearchError(f"Search failed: {e.response.status_code}")
        except Exception as e:
            logger.error(f"Azure Search error: {e}")
            raise AzureSearchError(f"Search failed: {str(e)}")

    def vector_search(
        self,
        vector: List[float],
        vector_field: str,
        k: int = 10,
        filter_expression: Optional[str] = None,
        select: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Perform a vector similarity search.

        Args:
            vector: Query vector (embeddings).
            vector_field: Name of the vector field to search.
            k: Number of nearest neighbors to return.
            filter_expression: OData filter expression.
            select: List of fields to return.

        Returns:
            Search results with documents and similarity scores.

        Raises:
            AzureSearchError: If search fails.
        """
        try:
            url = self._build_url(f"indexes/{self.index_name}/docs/search")
            headers = self._get_headers()

            payload = {
                "vectorQueries": [
                    {
                        "kind": "vector",
                        "vector": vector,
                        "fields": vector_field,
                        "k": k,
                    }
                ],
                "top": k,
            }

            if filter_expression:
                payload["filter"] = filter_expression
            if select:
                payload["select"] = ",".join(select)

            logger.debug(f"Vector search on index {self.index_name}, k={k}")

            with httpx.Client(timeout=30.0) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                return response.json()

        except httpx.HTTPStatusError as e:
            error_text = e.response.text if hasattr(e.response, "text") else str(e)
            logger.error(f"Vector search failed: {e.response.status_code} - {error_text}")
            raise AzureSearchError(f"Vector search failed: {e.response.status_code}")
        except Exception as e:
            logger.error(f"Azure Search error: {e}")
            raise AzureSearchError(f"Vector search failed: {str(e)}")

    def hybrid_search(
        self,
        search_text: str,
        vector: List[float],
        vector_field: str,
        k: int = 10,
        filter_expression: Optional[str] = None,
        select: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Perform a hybrid search combining keyword and vector search.

        Args:
            search_text: Search query text.
            vector: Query vector (embeddings).
            vector_field: Name of the vector field to search.
            k: Number of results to return.
            filter_expression: OData filter expression.
            select: List of fields to return.

        Returns:
            Search results with documents and scores.

        Raises:
            AzureSearchError: If search fails.
        """
        try:
            url = self._build_url(f"indexes/{self.index_name}/docs/search")
            headers = self._get_headers()

            payload = {
                "search": search_text,
                "vectorQueries": [
                    {
                        "kind": "vector",
                        "vector": vector,
                        "fields": vector_field,
                        "k": k,
                    }
                ],
                "top": k,
            }

            if filter_expression:
                payload["filter"] = filter_expression
            if select:
                payload["select"] = ",".join(select)

            logger.debug(f"Hybrid search on index {self.index_name}: {search_text}, k={k}")

            with httpx.Client(timeout=30.0) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                return response.json()

        except httpx.HTTPStatusError as e:
            error_text = e.response.text if hasattr(e.response, "text") else str(e)
            logger.error(f"Hybrid search failed: {e.response.status_code} - {error_text}")
            raise AzureSearchError(f"Hybrid search failed: {e.response.status_code}")
        except Exception as e:
            logger.error(f"Azure Search error: {e}")
            raise AzureSearchError(f"Hybrid search failed: {str(e)}")


def create_vector_search_config(
    algorithm_name: str = "hnsw-algorithm",
    profile_name: str = "vector-profile",
    metric: str = "cosine",
    m: int = 4,
    ef_construction: int = 400,
    ef_search: int = 500,
) -> Dict[str, Any]:
    """
    Helper function to create a vector search configuration.

    Args:
        algorithm_name: Name for the HNSW algorithm configuration.
        profile_name: Name for the vector search profile.
        metric: Distance metric (cosine, euclidean, dotProduct).
        m: HNSW M parameter (bi-directional links per node).
        ef_construction: HNSW ef during construction.
        ef_search: HNSW ef during search.

    Returns:
        Vector search configuration dict for index creation.
    """
    return {
        "algorithms": [
            {
                "name": algorithm_name,
                "kind": "hnsw",
                "hnswParameters": {
                    "m": m,
                    "efConstruction": ef_construction,
                    "efSearch": ef_search,
                    "metric": metric,
                },
            }
        ],
        "profiles": [
            {
                "name": profile_name,
                "algorithm": algorithm_name,
            }
        ],
    }
