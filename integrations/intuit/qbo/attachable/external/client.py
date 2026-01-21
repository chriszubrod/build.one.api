# Python Standard Library Imports
import json
import logging
from typing import Any, Dict, List, Optional

# Third-party Imports
import httpx

# Local Imports
from integrations.intuit.qbo.attachable.external.schemas import (
    QboAttachable,
    QboAttachableQueryResponse,
    QboAttachableResponse,
)
from integrations.intuit.qbo.base.errors import (
    QboError,
    QboAuthError,
    QboValidationError,
    QboRateLimitError,
    QboConflictError,
    QboNotFoundError,
)

logger = logging.getLogger(__name__)


class QboAttachableClient:
    """
    Lightweight client for interacting with QBO Attachable endpoints.
    """

    def __init__(
        self,
        *,
        access_token: str,
        realm_id: str,
        base_url: str = "https://quickbooks.api.intuit.com",
        minor_version: Optional[int] = 65,
        timeout: float = 30.0,
        session: Optional[httpx.Client] = None,
    ):
        self.access_token = access_token
        self.realm_id = realm_id
        self.minor_version = minor_version
        self._owns_client = session is None
        self._client = session or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self._client.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )

    def close(self) -> None:
        """Close the underlying HTTP client if we own it."""
        if self._owns_client and self._client:
            self._client.close()

    def __enter__(self) -> "QboAttachableClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _build_url(self, path: str) -> str:
        """Build full API URL for the given path."""
        base_path = f"/v3/company/{self.realm_id}/attachable"
        if self.minor_version:
            return f"{base_path}{path}?minorversion={self.minor_version}"
        return f"{base_path}{path}"

    def _request(self, method: str, path: str, **kwargs) -> Dict[str, Any]:
        """Make HTTP request and handle errors."""
        url = self._build_url(path)
        logger.debug(f"QBO Attachable API request: {method} {url}")

        try:
            response = self._client.request(method, url, **kwargs)
            logger.debug(f"QBO Attachable API response status: {response.status_code}")

            if response.status_code == 401:
                raise QboAuthError("Authentication failed. Access token may be expired.")
            elif response.status_code == 403:
                raise QboAuthError("Access forbidden. Check API permissions.")
            elif response.status_code == 404:
                raise QboNotFoundError("Attachable not found.")
            elif response.status_code == 409:
                raise QboConflictError("Conflict: Attachable may have been modified.")
            elif response.status_code == 429:
                raise QboRateLimitError("Rate limit exceeded. Try again later.")
            elif response.status_code >= 400:
                error_body = response.text
                logger.error(f"QBO API error: {response.status_code} - {error_body}")
                raise QboValidationError(f"API error {response.status_code}: {error_body}")

            return response.json()
        except httpx.RequestError as e:
            logger.error(f"HTTP request failed: {e}")
            raise QboError(f"HTTP request failed: {e}")

    def get_attachable(self, attachable_id: str) -> QboAttachable:
        """
        Get a single Attachable by ID.
        
        Args:
            attachable_id: QBO Attachable ID
            
        Returns:
            QboAttachable: The attachable record
        """
        path = f"/{attachable_id}"
        data = self._request("GET", path)
        response = QboAttachableResponse(**data)
        return response.attachable

    def query_attachables(
        self,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        start_position: int = 1,
        max_results: int = 1000,
    ) -> List[QboAttachable]:
        """
        Query attachables with optional filters.
        
        Args:
            entity_type: Filter by entity type (e.g., "Bill", "Invoice")
            entity_id: Filter by entity ID
            start_position: Starting position for pagination
            max_results: Maximum results to return
            
        Returns:
            List of QboAttachable records
        """
        # Build query - note: QBO Attachable queries are limited
        # We typically query all and filter, or use the AttachableRef field
        query_parts = ["SELECT * FROM Attachable"]
        
        # Note: QBO doesn't support direct WHERE clause on AttachableRef
        # We fetch and filter in application code
        
        query_parts.append(f"STARTPOSITION {start_position} MAXRESULTS {max_results}")
        query_string = " ".join(query_parts)

        # Use the query endpoint
        base_path = f"/v3/company/{self.realm_id}"
        query_url = f"{base_path}/query"
        if self.minor_version:
            query_url = f"{query_url}?minorversion={self.minor_version}"

        logger.debug(f"Querying Attachables with query: {query_string}")

        try:
            response = self._client.request("GET", query_url, params={"query": query_string})
            
            if response.status_code == 401:
                raise QboAuthError("Authentication failed.")
            elif response.status_code >= 400:
                error_body = response.text
                logger.error(f"QBO API error: {response.status_code} - {error_body}")
                raise QboValidationError(f"API error: {error_body}")

            data = response.json()
            query_response = data.get("QueryResponse", {})
            attachables_data = query_response.get("Attachable", [])
            
            attachables = [QboAttachable(**a) for a in attachables_data]
            
            # Filter by entity if specified
            if entity_type and entity_id:
                filtered = []
                for att in attachables:
                    if att.attachable_ref:
                        for ref in att.attachable_ref:
                            if ref.entity_ref_type == entity_type and ref.entity_ref_value == entity_id:
                                filtered.append(att)
                                break
                return filtered
            
            return attachables

        except httpx.RequestError as e:
            logger.error(f"HTTP request failed: {e}")
            raise QboError(f"HTTP request failed: {e}")

    def query_all_attachables(self) -> List[QboAttachable]:
        """
        Query all attachables with pagination.
        
        Returns:
            List of all QboAttachable records
        """
        all_attachables = []
        start_position = 1
        max_results = 1000

        while True:
            attachables = self.query_attachables(
                start_position=start_position,
                max_results=max_results,
            )
            if not attachables:
                break
            all_attachables.extend(attachables)
            if len(attachables) < max_results:
                break
            start_position += max_results

        logger.info(f"Retrieved {len(all_attachables)} attachables from QBO")
        return all_attachables

    def query_attachables_for_entity(
        self,
        entity_type: str,
        entity_id: str,
    ) -> List[QboAttachable]:
        """
        Query attachables linked to a specific entity.
        
        Args:
            entity_type: Entity type (e.g., "Bill", "Invoice")
            entity_id: Entity ID
            
        Returns:
            List of QboAttachable records linked to the entity
        """
        # QBO doesn't support direct filtering on AttachableRef in queries
        # Use the dedicated endpoint for fetching attachables by entity
        base_path = f"/v3/company/{self.realm_id}"
        # Use entity query approach
        query_string = f"SELECT * FROM Attachable WHERE AttachableRef.EntityRef.Type = '{entity_type}' AND AttachableRef.EntityRef.Value = '{entity_id}'"
        
        query_url = f"{base_path}/query"
        if self.minor_version:
            query_url = f"{query_url}?minorversion={self.minor_version}"

        logger.debug(f"Querying Attachables for {entity_type} {entity_id}")

        try:
            response = self._client.request("GET", query_url, params={"query": query_string})
            
            if response.status_code == 401:
                raise QboAuthError("Authentication failed.")
            elif response.status_code >= 400:
                # If the query syntax isn't supported, fall back to filtering
                logger.warning(f"Direct AttachableRef query not supported, falling back to filter")
                return self.query_attachables(entity_type=entity_type, entity_id=entity_id)

            data = response.json()
            query_response = data.get("QueryResponse", {})
            attachables_data = query_response.get("Attachable", [])
            
            return [QboAttachable(**a) for a in attachables_data]

        except httpx.RequestError as e:
            logger.error(f"HTTP request failed: {e}")
            raise QboError(f"HTTP request failed: {e}")

    def download_attachable(self, attachable: QboAttachable) -> Optional[bytes]:
        """
        Download the file content for an attachable.
        
        Args:
            attachable: QboAttachable with TempDownloadUri
            
        Returns:
            bytes: File content, or None if download fails
        """
        download_uri = attachable.temp_download_uri or attachable.file_access_uri
        if not download_uri:
            logger.warning(f"No download URI for attachable {attachable.id}")
            return None

        try:
            # Use a separate client for downloading as the URL is absolute
            with httpx.Client(timeout=60.0) as download_client:
                response = download_client.get(download_uri)
                
                if response.status_code == 200:
                    logger.debug(f"Downloaded attachable {attachable.id}: {len(response.content)} bytes")
                    return response.content
                else:
                    logger.error(f"Failed to download attachable {attachable.id}: {response.status_code}")
                    return None

        except httpx.RequestError as e:
            logger.error(f"Failed to download attachable {attachable.id}: {e}")
            return None

    def upload_attachable(
        self,
        file_content: bytes,
        filename: str,
        content_type: str,
        entity_type: str,
        entity_id: str,
        note: Optional[str] = None,
    ) -> QboAttachable:
        """
        Upload a file to QBO and link it to an entity (e.g., Bill).
        
        Uses multipart/form-data upload to /v3/company/{realmId}/upload endpoint.
        
        Args:
            file_content: File content as bytes
            filename: Name of the file (including extension)
            content_type: MIME type of the file (e.g., "application/pdf")
            entity_type: Type of entity to link to (e.g., "Bill", "Invoice")
            entity_id: QBO ID of the entity to link to
            note: Optional note/description for the attachment
            
        Returns:
            QboAttachable: The created attachable record
            
        Raises:
            QboValidationError: If upload fails
            QboAuthError: If authentication fails
        """
        # Build the upload URL
        upload_url = f"/v3/company/{self.realm_id}/upload"
        if self.minor_version:
            upload_url = f"{upload_url}?minorversion={self.minor_version}"
        
        # Build the AttachableRef metadata as JSON
        attachable_metadata = {
            "AttachableRef": [
                {
                    "EntityRef": {
                        "type": entity_type,
                        "value": entity_id,
                    }
                }
            ],
            "FileName": filename,
            "ContentType": content_type,
        }
        
        if note:
            attachable_metadata["Note"] = note
        
        logger.debug(f"Uploading attachable '{filename}' to {entity_type} {entity_id}")
        
        try:
            # Build multipart form data
            # QBO expects:
            # - 'file_metadata_01': JSON metadata including AttachableRef
            # - 'file_content_01': The actual file content
            files = {
                "file_metadata_01": (
                    None,
                    json.dumps(attachable_metadata),
                    "application/json",
                ),
                "file_content_01": (
                    filename,
                    file_content,
                    content_type,
                ),
            }
            
            # Create a new client for multipart upload (different headers)
            with httpx.Client(
                base_url=self._client.base_url,
                timeout=120.0,  # Longer timeout for uploads
            ) as upload_client:
                response = upload_client.post(
                    upload_url,
                    files=files,
                    headers={
                        "Authorization": f"Bearer {self.access_token}",
                        "Accept": "application/json",
                        # Note: Content-Type is set automatically for multipart
                    },
                )
                
                logger.debug(f"Upload response status: {response.status_code}")
                
                if response.status_code == 401:
                    raise QboAuthError("Authentication failed. Access token may be expired.")
                elif response.status_code == 403:
                    raise QboAuthError("Access forbidden. Check API permissions.")
                elif response.status_code == 429:
                    raise QboRateLimitError("Rate limit exceeded. Try again later.")
                elif response.status_code >= 400:
                    error_body = response.text
                    logger.error(f"QBO upload error: {response.status_code} - {error_body}")
                    raise QboValidationError(f"Upload failed {response.status_code}: {error_body}")
                
                data = response.json()
                
                # Response contains AttachableResponse wrapper
                attachable_response = data.get("AttachableResponse", [])
                if attachable_response and len(attachable_response) > 0:
                    attachable_data = attachable_response[0].get("Attachable")
                    if attachable_data:
                        attachable = QboAttachable(**attachable_data)
                        logger.info(f"Successfully uploaded attachable {attachable.id} for {entity_type} {entity_id}")
                        return attachable
                
                # Fallback: try direct Attachable key
                if "Attachable" in data:
                    attachable = QboAttachable(**data["Attachable"])
                    logger.info(f"Successfully uploaded attachable {attachable.id} for {entity_type} {entity_id}")
                    return attachable
                
                logger.error(f"Unexpected upload response format: {data}")
                raise QboValidationError(f"Unexpected upload response format")
                
        except httpx.RequestError as e:
            logger.error(f"HTTP request failed during upload: {e}")
            raise QboError(f"HTTP request failed: {e}")
