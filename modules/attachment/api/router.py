# Python Standard Library Imports
import io
import logging
import uuid
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse

# Local Imports
from modules.attachment.api.schemas import AttachmentCreate, AttachmentUpdate
from modules.attachment.business.service import AttachmentService
from modules.auth.business.service import get_current_user_api as get_current_attachment_api
from shared.storage import AzureBlobStorage, AzureBlobStorageError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "attachment"])
service = AttachmentService()


@router.post("/create/attachment")
def create_attachment_router(body: AttachmentCreate, current_user: dict = Depends(get_current_attachment_api)):
    """
    Create a new attachment (metadata only, upload handled separately).
    """
    try:
        attachment = service.create(
            filename=body.filename,
            original_filename=body.original_filename,
            file_extension=body.file_extension,
            content_type=body.content_type,
            file_size=body.file_size,
            file_hash=body.file_hash,
            blob_url=body.blob_url,
            description=body.description,
            category=body.category,
            tags=body.tags,
            is_archived=body.is_archived or False,
            status=body.status,
            expiration_date=body.expiration_date,
            storage_tier=body.storage_tier or "Hot",
        )
        return attachment.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/attachments")
def get_attachments_router(
    category: Optional[str] = None,
    is_archived: Optional[bool] = None,
    status: Optional[str] = None,
    current_user: dict = Depends(get_current_attachment_api),
):
    """
    Read all attachments with optional filters.
    """
    try:
        if category:
            attachments = service.read_by_category(category)
        else:
            attachments = service.read_all()
        
        # Apply filters
        if is_archived is not None:
            attachments = [a for a in attachments if a.is_archived == is_archived]
        if status:
            attachments = [a for a in attachments if a.status == status]
        
        return [attachment.to_dict() for attachment in attachments]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/attachment/{public_id}")
def get_attachment_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_attachment_api)):
    """
    Read an attachment by public ID.
    """
    try:
        attachment = service.read_by_public_id(public_id=public_id)
        if not attachment:
            raise HTTPException(status_code=404, detail="Attachment not found")
        return attachment.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/attachments/by-category/{category}")
def get_attachments_by_category_router(category: str, current_user: dict = Depends(get_current_attachment_api)):
    """
    List attachments by category.
    """
    try:
        attachments = service.read_by_category(category)
        return [attachment.to_dict() for attachment in attachments]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/attachments/by-hash/{hash}")
def get_attachment_by_hash_router(hash: str, current_user: dict = Depends(get_current_attachment_api)):
    """
    Find duplicate attachments by hash.
    """
    try:
        attachment = service.read_by_hash(hash)
        if not attachment:
            raise HTTPException(status_code=404, detail="Attachment not found")
        return attachment.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/update/attachment/{public_id}")
def update_attachment_by_public_id_router(
    public_id: str, body: AttachmentUpdate, current_user: dict = Depends(get_current_attachment_api)
):
    """
    Update an attachment by public ID.
    """
    try:
        attachment = service.update_by_public_id(public_id=public_id, attachment=body)
        if not attachment:
            raise HTTPException(status_code=404, detail="Attachment not found")
        return attachment.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/archive/attachment/{public_id}")
def archive_attachment_router(public_id: str, current_user: dict = Depends(get_current_attachment_api)):
    """
    Archive an attachment (soft delete).
    """
    try:
        attachment = service.archive(public_id=public_id)
        if not attachment:
            raise HTTPException(status_code=404, detail="Attachment not found")
        return attachment.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/unarchive/attachment/{public_id}")
def unarchive_attachment_router(public_id: str, current_user: dict = Depends(get_current_attachment_api)):
    """
    Unarchive an attachment.
    """
    try:
        attachment = service.unarchive(public_id=public_id)
        if not attachment:
            raise HTTPException(status_code=404, detail="Attachment not found")
        return attachment.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete/attachment/{public_id}")
def delete_attachment_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_attachment_api)):
    """
    Delete an attachment by public ID (and blob).
    """
    try:
        attachment = service.read_by_public_id(public_id=public_id)
        if not attachment:
            raise HTTPException(status_code=404, detail="Attachment not found")
        
        # Delete blob from Azure Storage
        if attachment.blob_url:
            try:
                storage = AzureBlobStorage()
                storage.delete_file(attachment.blob_url)
            except AzureBlobStorageError as e:
                logger.warning(f"Failed to delete blob: {e}")
                # Continue with database deletion even if blob deletion fails
        
        # Delete from database
        deleted = service.delete_by_public_id(public_id=public_id)
        return deleted.to_dict() if deleted else {}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload/attachment")
async def upload_attachment_router(
    file: UploadFile = File(...),
    description: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    status: Optional[str] = Form(None),
    expiration_date: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_attachment_api),
):
    """
    Upload a file to blob storage and create attachment record.
    """
    try:
        # Read file content
        file_content = await file.read()
        file_size = len(file_content)
        
        # Validate file size
        service.validate_file_size(file_size)
        
        # Calculate hash
        file_hash = service.calculate_hash(file_content)
        
        # Check for duplicates (optional - can warn or prevent)
        existing = service.read_by_hash(file_hash)
        if existing:
            logger.info(f"Duplicate file detected: {existing.public_id}")
            # Return existing attachment instead of creating duplicate
            # Uncomment to prevent duplicates:
            # raise HTTPException(status_code=409, detail="File already exists")
        
        # Extract file extension
        file_extension = service.extract_extension(file.filename or "")
        
        # Generate unique blob name using public_id
        public_id = str(uuid.uuid4())
        blob_name = f"{public_id}_{file.filename or 'file'}"
        
        # Upload to Azure Blob Storage
        storage = AzureBlobStorage()
        blob_url = storage.upload_file(
            blob_name=blob_name,
            file_content=file_content,
            content_type=file.content_type or "application/octet-stream",
        )
        
        # Create attachment record
        attachment = service.create(
            filename=file.filename,
            original_filename=file.filename,
            file_extension=file_extension,
            content_type=file.content_type or "application/octet-stream",
            file_size=file_size,
            file_hash=file_hash,
            blob_url=blob_url,
            description=description,
            category=category,
            tags=tags,
            is_archived=False,
            status=status,
            expiration_date=expiration_date,
            storage_tier="Hot",
        )
        
        return attachment.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except AzureBlobStorageError as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload to blob storage: {str(e)}")
    except Exception as e:
        logger.error(f"Error uploading attachment: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download/attachment/{public_id}")
def download_attachment_router(public_id: str, current_user: dict = Depends(get_current_attachment_api)):
    """
    Download a file from blob storage (increments download count).
    """
    try:
        attachment = service.read_by_public_id(public_id=public_id)
        if not attachment:
            raise HTTPException(status_code=404, detail="Attachment not found")
        
        # Check if archived
        if attachment.is_archived:
            raise HTTPException(status_code=404, detail="Attachment is archived")
        
        # Check expiration (warn but allow download)
        is_expired = service.is_expired(attachment)
        
        # Increment download count
        service.increment_download_count(public_id=public_id)
        
        # Download from Azure Blob Storage
        storage = AzureBlobStorage()
        file_content, metadata = storage.download_file(attachment.blob_url)
        
        # Return file with proper headers
        return StreamingResponse(
            io.BytesIO(file_content),
            media_type=metadata.get("content_type", attachment.content_type or "application/octet-stream"),
            headers={
                "Content-Disposition": f'attachment; filename="{attachment.original_filename or attachment.filename}"',
                "X-Attachment-Expired": "true" if is_expired else "false",
            },
        )
    except HTTPException:
        raise
    except AzureBlobStorageError as e:
        logger.error(f"Error downloading blob: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to download from blob storage: {str(e)}")
    except Exception as e:
        logger.error(f"Error downloading attachment: {e}")
        raise HTTPException(status_code=500, detail=str(e))

