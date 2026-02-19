# Python Standard Library Imports
import base64
import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Optional, Tuple
from urllib.parse import quote, unquote, urlparse, parse_qs

# Third-party Imports
import httpx

# Local Imports
import config

logger = logging.getLogger(__name__)


class AzureBlobStorageError(Exception):
    """Base exception for Azure Blob Storage operations."""
    pass


class AzureBlobStorage:
    """
    Azure Blob Storage integration using REST API (no SDK).
    Supports Shared Key and SAS token authentication.
    """

    def __init__(self):
        """Initialize Azure Blob Storage client."""
        settings = config.Settings()
        self.account_name = settings.azure_storage_account_name
        self.account_key = settings.azure_storage_account_key
        self.sas_token = settings.azure_storage_sas_token
        self.container_name = settings.azure_storage_container_name
        self.timeout = settings.azure_storage_timeout
        
        if not self.account_name:
            raise ValueError("Azure Storage account name is required")
        
        if not self.account_key and not self.sas_token:
            raise ValueError("Either Azure Storage account key or SAS token is required")
        
        self.base_url = f"https://{self.account_name}.blob.core.windows.net"
        self.api_version = "2021-04-10"

    def _generate_shared_key_signature(
        self,
        method: str,
        url_path: str,
        headers: dict,
        content_length: int = 0
    ) -> str:
        """
        Generate Shared Key signature for Azure Blob Storage REST API.
        
        Args:
            method: HTTP method (PUT, GET, DELETE)
            url_path: URL path including query string
            headers: Request headers
            content_length: Content length in bytes
            
        Returns:
            Authorization header value
        """
        # Canonicalized headers - must include ALL x-ms-* headers, sorted alphabetically
        canonical_headers = []
        xms_headers = []
        for name, value in headers.items():
            if name.lower().startswith("x-ms-"):
                xms_headers.append((name.lower(), value))
        # Sort by header name
        xms_headers.sort(key=lambda x: x[0])
        for name, value in xms_headers:
            canonical_headers.append(f"{name}:{value}")
        canonical_headers_str = "\n".join(canonical_headers) + "\n" if canonical_headers else "\n"
        
        # Parse URL path and query string for canonicalized resource
        parsed = urlparse(url_path if url_path.startswith('/') else f"/{url_path}")
        path = parsed.path
        
        # Build canonicalized resource: /account_name/path
        canonical_resource = f"/{self.account_name}{path}"
        
        # Add query parameters (formatted as param:value on separate lines, sorted)
        query_params = parse_qs(parsed.query)
        if query_params:
            # Sort query parameters by name
            sorted_params = sorted(query_params.items())
            for param_name, param_values in sorted_params:
                # Take first value if multiple values exist
                param_value = param_values[0] if param_values else ""
                # Format as param:value (colon, not equals)
                canonical_resource += f"\n{param_name}:{param_value}"
        
        # String to sign - must match Azure's exact format
        # Content-Length should be empty string when 0, not "0"
        content_length_str = "" if content_length == 0 else str(content_length)
        string_to_sign = (
            f"{method}\n"  # HTTP verb
            f"\n"  # Content-Encoding
            f"\n"  # Content-Language
            f"{content_length_str}\n"  # Content-Length (empty when 0)
            f"\n"  # Content-MD5
            f"{headers.get('Content-Type', '')}\n"  # Content-Type
            f"\n"  # Date
            f"\n"  # If-Modified-Since
            f"\n"  # If-Match
            f"\n"  # If-None-Match
            f"\n"  # If-Unmodified-Since
            f"\n"  # Range
            f"{canonical_headers_str}"  # Canonicalized headers (all x-ms-* headers)
            f"{canonical_resource}"  # Canonicalized resource
        )
        
        # Sign with account key
        account_key_bytes = base64.b64decode(self.account_key)
        signature = base64.b64encode(
            hmac.new(account_key_bytes, string_to_sign.encode('utf-8'), hashlib.sha256).digest()
        ).decode('utf-8')
        
        return f"SharedKey {self.account_name}:{signature}"

    def _get_headers(self, method: str, content_type: Optional[str] = None, content_length: int = 0) -> dict:
        """Get standard headers for Azure Blob Storage requests."""
        headers = {
            "x-ms-date": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT"),
            "x-ms-version": self.api_version,
        }
        
        if content_type:
            headers["Content-Type"] = content_type
        if content_length > 0:
            headers["Content-Length"] = str(content_length)
        
        return headers

    def _build_url(self, blob_name: str, use_sas: bool = False, container_name: Optional[str] = None) -> str:
        """Build the full URL for a blob."""
        container = container_name or self.container_name
        blob_name_encoded = quote(blob_name, safe="/")
        url = f"{self.base_url}/{container}/{blob_name_encoded}"
        
        if use_sas and self.sas_token:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{self.sas_token}"
        
        return url

    def upload_file(
        self,
        blob_name: str,
        file_content: bytes,
        content_type: str = "application/octet-stream",
        container_name: Optional[str] = None
    ) -> str:
        """
        Upload a file to Azure Blob Storage.
        
        Args:
            blob_name: Name of the blob (can include path)
            file_content: File content as bytes
            content_type: MIME type of the file
            container_name: Optional container name (defaults to configured container)
            
        Returns:
            Full URL of the uploaded blob
            
        Raises:
            AzureBlobStorageError: If upload fails
        """
        try:
            container = container_name or self.container_name
            url = self._build_url(blob_name, use_sas=bool(self.sas_token), container_name=container)
            content_length = len(file_content)
            headers = self._get_headers("PUT", content_type, content_length)
            
            # Add blob-specific headers
            headers["x-ms-blob-type"] = "BlockBlob"
            
            # Add authentication
            if self.account_key and not self.sas_token:
                url_path = f"/{container}/{quote(blob_name, safe='/')}"
                headers["Authorization"] = self._generate_shared_key_signature(
                    "PUT", url_path, headers, content_length
                )
            
            # Ensure container exists (create if needed) - must succeed before upload
            self._ensure_container_exists(container)
            
            # Upload blob
            with httpx.Client(timeout=30.0) as client:
                response = client.put(url, content=file_content, headers=headers)
                response.raise_for_status()
            
            return self._build_url(blob_name, use_sas=False, container_name=container)  # Return URL without SAS token
            
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error uploading blob {blob_name}: {e.response.status_code} - {e.response.text}")
            raise AzureBlobStorageError(f"Failed to upload blob: {e.response.status_code}")
        except Exception as e:
            logger.error(f"Error uploading blob {blob_name}: {e}")
            raise AzureBlobStorageError(f"Failed to upload blob: {str(e)}")

    def _parse_blob_url(self, blob_url: str) -> Tuple[str, str]:
        """
        Parse a blob URL to extract container name and blob name.
        
        Args:
            blob_url: Full URL of the blob
            
        Returns:
            Tuple of (container_name, blob_name)
        """
        parsed = urlparse(blob_url)
        # Path format: /container_name/blob_name
        path_parts = parsed.path.lstrip("/").split("/", 1)
        if len(path_parts) == 2:
            # URL-decode the blob name to avoid double-encoding
            blob_name = unquote(path_parts[1])
            return path_parts[0], blob_name
        elif len(path_parts) == 1:
            # Assume default container if only blob name in path
            blob_name = unquote(path_parts[0])
            return self.container_name, blob_name
        else:
            raise AzureBlobStorageError(f"Invalid blob URL format: {blob_url}")

    def download_file(self, blob_url: str) -> Tuple[bytes, dict]:
        """
        Download a file from Azure Blob Storage.
        
        Args:
            blob_url: Full URL of the blob
            
        Returns:
            Tuple of (file_content, metadata_dict)
            
        Raises:
            AzureBlobStorageError: If download fails
        """
        try:
            # Parse URL to extract container and blob name
            container, blob_name = self._parse_blob_url(blob_url)
            
            url = self._build_url(blob_name, use_sas=bool(self.sas_token), container_name=container)
            headers = self._get_headers("GET")
            
            # Add authentication if using Shared Key
            if self.account_key and not self.sas_token:
                url_path = f"/{container}/{quote(blob_name, safe='/')}"
                headers["Authorization"] = self._generate_shared_key_signature("GET", url_path, headers)
            
            with httpx.Client(timeout=30.0) as client:
                response = client.get(url, headers=headers)
                response.raise_for_status()
            
            # Extract metadata from response headers
            metadata = {
                "content_type": response.headers.get("Content-Type", "application/octet-stream"),
                "content_length": int(response.headers.get("Content-Length", 0)),
                "last_modified": response.headers.get("Last-Modified"),
                "etag": response.headers.get("ETag"),
            }
            
            return response.content, metadata
            
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error downloading blob {blob_url}: {e.response.status_code} - {e.response.text}")
            raise AzureBlobStorageError(f"Failed to download blob: {e.response.status_code}")
        except Exception as e:
            logger.error(f"Error downloading blob {blob_url}: {e}")
            raise AzureBlobStorageError(f"Failed to download blob: {str(e)}")

    def delete_file(self, blob_url: str) -> None:
        """
        Delete a file from Azure Blob Storage.
        
        Args:
            blob_url: Full URL of the blob
            
        Raises:
            AzureBlobStorageError: If deletion fails
        """
        try:
            # Parse URL to extract container and blob name
            container, blob_name = self._parse_blob_url(blob_url)
            
            url = self._build_url(blob_name, use_sas=bool(self.sas_token), container_name=container)
            headers = self._get_headers("DELETE")
            
            # Add authentication if using Shared Key
            if self.account_key and not self.sas_token:
                url_path = f"/{container}/{quote(blob_name, safe='/')}"
                headers["Authorization"] = self._generate_shared_key_signature("DELETE", url_path, headers)
            
            with httpx.Client(timeout=30.0) as client:
                response = client.delete(url, headers=headers)
                # 404 is acceptable (blob already deleted)
                if response.status_code not in (202, 204, 404):
                    response.raise_for_status()
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.info(f"Blob {blob_url} not found (may already be deleted)")
                return
            logger.error(f"HTTP error deleting blob {blob_url}: {e.response.status_code} - {e.response.text}")
            raise AzureBlobStorageError(f"Failed to delete blob: {e.response.status_code}")
        except Exception as e:
            logger.error(f"Error deleting blob {blob_url}: {e}")
            raise AzureBlobStorageError(f"Failed to delete blob: {str(e)}")

    def _ensure_container_exists(self, container_name: Optional[str] = None) -> None:
        """Ensure the container exists, create if it doesn't."""
        try:
            container = container_name or self.container_name
            url = f"{self.base_url}/{container}?restype=container"
            headers = self._get_headers("PUT")
            
            # Container creation doesn't need content-type or content-length
            # Remove them if they were added
            headers.pop("Content-Type", None)
            headers.pop("Content-Length", None)
            
            if self.account_key and not self.sas_token:
                url_path = f"/{container}?restype=container"
                # For container creation, content_length is 0
                headers["Authorization"] = self._generate_shared_key_signature("PUT", url_path, headers, content_length=0)
            elif self.sas_token:
                separator = "&" if "?" in url else "?"
                url = f"{url}{separator}{self.sas_token}"
            
            with httpx.Client(timeout=30.0) as client:
                response = client.put(url, headers=headers)
                # 201 = created, 409 = already exists
                if response.status_code not in (201, 409):
                    error_text = response.text if hasattr(response, 'text') else str(response.status_code)
                    logger.error(f"Failed to create container: {response.status_code} - {error_text}")
                    response.raise_for_status()
                logger.info(f"Container '{container}' exists or was created successfully")
        except httpx.HTTPStatusError as e:
            error_text = e.response.text if hasattr(e.response, 'text') else str(e.response.status_code)
            logger.error(f"HTTP error creating container: {e.response.status_code} - {error_text}")
            raise AzureBlobStorageError(f"Failed to create container: {e.response.status_code}")
        except Exception as e:
            logger.error(f"Error creating container: {e}")
            raise AzureBlobStorageError(f"Failed to create container: {str(e)}")

