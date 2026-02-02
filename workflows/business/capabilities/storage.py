# Python Standard Library Imports
import logging
import uuid
from typing import Optional, Tuple

# Local Imports
from workflows.capabilities.base import Capability, CapabilityResult, with_retry

logger = logging.getLogger(__name__)


class StorageCapabilities(Capability):
    """
    Azure Blob Storage capabilities.
    
    Provides file upload and download operations.
    """
    
    @property
    def name(self) -> str:
        return "storage"
    
    def __init__(self):
        self._client = None
    
    def _get_client(self):
        """Lazy load the blob storage client."""
        if self._client is None:
            from shared.storage import AzureBlobStorage
            self._client = AzureBlobStorage()
        return self._client
    
    @with_retry(max_attempts=3, base_delay=1.0)
    def save_blob(
        self,
        file_content: bytes,
        filename: str,
        container_name: Optional[str] = None,
        content_type: str = "application/octet-stream",
        path_prefix: Optional[str] = None,
    ) -> CapabilityResult:
        """
        Save a file to blob storage.
        
        Args:
            file_content: File bytes to upload
            filename: Original filename
            container_name: Optional container (defaults to attachments)
            content_type: MIME type
            path_prefix: Optional path prefix (e.g., "workflows/123")
            
        Returns:
            CapabilityResult with blob URL
        """
        self._log_call(
            "save_blob",
            filename=filename,
            content_size=len(file_content),
            content_type=content_type,
        )
        
        try:
            client = self._get_client()
            
            # Generate unique blob name
            unique_id = str(uuid.uuid4())[:8]
            if path_prefix:
                blob_name = f"{path_prefix}/{unique_id}_{filename}"
            else:
                blob_name = f"workflows/{unique_id}_{filename}"
            
            blob_url = client.upload_file(
                blob_name=blob_name,
                file_content=file_content,
                content_type=content_type,
                container_name=container_name,
            )
            
            result = CapabilityResult.ok(
                data={
                    "blob_url": blob_url,
                    "blob_name": blob_name,
                    "size": len(file_content),
                },
            )
            self._log_result("save_blob", result)
            return result
            
        except Exception as e:
            return self._handle_error(e, "save_blob")
    
    @with_retry(max_attempts=3, base_delay=1.0)
    def download_blob(
        self,
        blob_url: str,
    ) -> CapabilityResult:
        """
        Download a file from blob storage.
        
        Args:
            blob_url: URL of the blob to download
            
        Returns:
            CapabilityResult with file bytes and metadata
        """
        self._log_call("download_blob", blob_url=blob_url)
        
        try:
            client = self._get_client()
            
            content, metadata = client.download_file(blob_url)
            
            result = CapabilityResult.ok(
                data={
                    "content": content,
                    "content_type": metadata.get("content_type"),
                    "size": len(content),
                },
            )
            self._log_result("download_blob", result)
            return result
            
        except Exception as e:
            return self._handle_error(e, "download_blob")
    
    @with_retry(max_attempts=3, base_delay=1.0)
    def delete_blob(
        self,
        blob_url: str,
    ) -> CapabilityResult:
        """
        Delete a file from blob storage.
        
        Args:
            blob_url: URL of the blob to delete
            
        Returns:
            CapabilityResult with success status
        """
        self._log_call("delete_blob", blob_url=blob_url)
        
        try:
            client = self._get_client()
            client.delete_file(blob_url)
            
            result = CapabilityResult.ok(data={"deleted": True})
            self._log_result("delete_blob", result)
            return result
            
        except Exception as e:
            return self._handle_error(e, "delete_blob")
    
    def save_workflow_attachment(
        self,
        workflow_public_id: str,
        file_content: bytes,
        filename: str,
        content_type: str = "application/octet-stream",
    ) -> CapabilityResult:
        """
        Save a workflow attachment with organized path.
        
        Args:
            workflow_public_id: Workflow public ID for path organization
            file_content: File bytes
            filename: Original filename
            content_type: MIME type
            
        Returns:
            CapabilityResult with blob URL
        """
        return self.save_blob(
            file_content=file_content,
            filename=filename,
            content_type=content_type,
            path_prefix=f"workflows/{workflow_public_id}",
        )
