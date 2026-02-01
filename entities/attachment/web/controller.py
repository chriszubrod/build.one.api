# Python Standard Library Imports
import io
import logging

# Third-party Imports
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import RedirectResponse, StreamingResponse

# Local Imports
from entities.auth.business.service import get_current_user_web
from entities.attachment.business.service import AttachmentService
from shared.storage import AzureBlobStorage, AzureBlobStorageError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/attachment", tags=["web", "attachment"])


@router.get("/view/{public_id}")
def view_attachment(public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    View an attachment file in the browser (uses session auth).
    """
    try:
        service = AttachmentService()
        attachment = service.read_by_public_id(public_id=public_id)
        
        if not attachment:
            raise HTTPException(status_code=404, detail="Attachment not found")
        
        if attachment.is_archived:
            raise HTTPException(status_code=404, detail="Attachment is archived")
        
        # Download from Azure Blob Storage
        storage = AzureBlobStorage()
        file_content, metadata = storage.download_file(attachment.blob_url)
        
        # Return file with inline disposition (viewable in browser)
        return StreamingResponse(
            io.BytesIO(file_content),
            media_type=metadata.get("content_type", attachment.content_type or "application/octet-stream"),
            headers={
                "Content-Disposition": f'inline; filename="{attachment.original_filename or attachment.filename}"',
            },
        )
    except HTTPException:
        raise
    except AzureBlobStorageError as e:
        logger.error(f"Error viewing blob: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to view attachment: {str(e)}")
    except Exception as e:
        logger.error(f"Error viewing attachment: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download/{public_id}")
def download_attachment(public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    Download an attachment file (uses session auth).
    """
    try:
        service = AttachmentService()
        attachment = service.read_by_public_id(public_id=public_id)
        
        if not attachment:
            raise HTTPException(status_code=404, detail="Attachment not found")
        
        if attachment.is_archived:
            raise HTTPException(status_code=404, detail="Attachment is archived")
        
        # Increment download count
        service.increment_download_count(public_id=public_id)
        
        # Download from Azure Blob Storage
        storage = AzureBlobStorage()
        file_content, metadata = storage.download_file(attachment.blob_url)
        
        # Return file with attachment disposition (forces download)
        return StreamingResponse(
            io.BytesIO(file_content),
            media_type=metadata.get("content_type", attachment.content_type or "application/octet-stream"),
            headers={
                "Content-Disposition": f'attachment; filename="{attachment.original_filename or attachment.filename}"',
            },
        )
    except HTTPException:
        raise
    except AzureBlobStorageError as e:
        logger.error(f"Error downloading blob: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to download attachment: {str(e)}")
    except Exception as e:
        logger.error(f"Error downloading attachment: {e}")
        raise HTTPException(status_code=500, detail=str(e))

