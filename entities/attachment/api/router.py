# Python Standard Library Imports
import io
import logging
import uuid
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, Form, status
from fastapi.responses import StreamingResponse

# Local Imports
from entities.attachment.api.schemas import AttachmentCreate, AttachmentUpdate
from entities.attachment.business.service import AttachmentService
from entities.attachment.business.extraction_service import ExtractionService
from entities.auth.business.service import get_current_user_api as get_current_attachment_api
from shared.storage import AzureBlobStorage, AzureBlobStorageError
from workflows.router import TriggerRouter, TriggerContext, TriggerType, TriggerSource

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "attachment"])
service = AttachmentService()
extraction_service = ExtractionService()


def trigger_extraction_background(attachment_id: int):
    """Background task to extract text from an attachment."""
    try:
        logger.info(f"Starting background extraction for attachment {attachment_id}")
        extraction_service.extract_attachment_by_id(attachment_id)
        logger.info(f"Completed background extraction for attachment {attachment_id}")
    except Exception as e:
        logger.error(f"Background extraction failed for attachment {attachment_id}: {e}")


@router.post("/create/attachment")
def create_attachment_router(body: AttachmentCreate, current_user: dict = Depends(get_current_attachment_api)):
    """
    Create a new attachment (metadata only, upload handled separately).
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "filename": body.filename,
            "original_filename": body.original_filename,
            "file_extension": body.file_extension,
            "content_type": body.content_type,
            "file_size": body.file_size,
            "file_hash": body.file_hash,
            "blob_url": body.blob_url,
            "description": body.description,
            "category": body.category,
            "tags": body.tags,
            "is_archived": body.is_archived or False,
            "status": body.status,
            "expiration_date": body.expiration_date,
            "storage_tier": body.storage_tier or "Hot",
        },
        workflow_type="attachment_create",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create attachment")
        )
    
    return result.get("data")


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
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "row_version": body.row_version,
            "filename": body.filename,
            "original_filename": body.original_filename,
            "file_extension": body.file_extension,
            "content_type": body.content_type,
            "file_size": body.file_size,
            "file_hash": body.file_hash,
            "blob_url": body.blob_url,
            "description": body.description,
            "category": body.category,
            "tags": body.tags,
            "is_archived": body.is_archived,
            "status": body.status,
            "expiration_date": body.expiration_date,
            "storage_tier": body.storage_tier,
        },
        workflow_type="attachment_update",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update attachment")
        )
    
    return result.get("data")


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
    background_tasks: BackgroundTasks,
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
    Text extraction runs in background for supported file types.
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
        
        # Generate unique blob name using public_id only (with extension)
        public_id = str(uuid.uuid4())
        blob_name = f"{public_id}{file_extension}" if file_extension else public_id
        
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
        
        # Trigger background extraction for supported file types
        if attachment.id and extraction_service.is_extractable(attachment):
            background_tasks.add_task(trigger_extraction_background, attachment.id)
            logger.info(f"Queued background extraction for attachment {attachment.id}")
        
        return attachment.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except AzureBlobStorageError as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload to blob storage: {str(e)}")
    except Exception as e:
        logger.error(f"Error uploading attachment: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload/bill-line-item-attachment")
async def upload_bill_line_item_attachment_router(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    description: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_attachment_api),
):
    """
    Upload a bill line item attachment to Azure Blob Storage only.
    SharePoint upload happens later when 'Complete Bill' is clicked (using final Bill/BillLineItem values).
    Text extraction runs in background for supported file types.
    """
    try:
        # Read file content
        file_content = await file.read()
        file_size = len(file_content)
        
        # Validate file size
        service.validate_file_size(file_size)
        
        # Calculate hash
        file_hash = service.calculate_hash(file_content)
        
        # Extract file extension
        file_extension = service.extract_extension(file.filename or "")
        
        # Generate unique blob name using public_id only (with extension)
        public_id = str(uuid.uuid4())
        blob_name = f"{public_id}{file_extension}" if file_extension else public_id
        
        # Upload to Azure Blob Storage
        storage = AzureBlobStorage()
        blob_url = storage.upload_file(
            blob_name=blob_name,
            file_content=file_content,
            content_type=file.content_type or "application/octet-stream",
        )
        logger.info(f"Uploaded bill line item attachment to Azure Blob: {blob_url}")
        
        # Create attachment record in database
        attachment = service.create(
            filename=file.filename,
            original_filename=file.filename,
            file_extension=file_extension,
            content_type=file.content_type or "application/octet-stream",
            file_size=file_size,
            file_hash=file_hash,
            blob_url=blob_url,
            description=description,
            category="bill_line_item",
            tags=None,
            is_archived=False,
            status=None,
            expiration_date=None,
            storage_tier="Hot",
        )
        
        # Trigger background extraction for supported file types
        if attachment.id and extraction_service.is_extractable(attachment):
            background_tasks.add_task(trigger_extraction_background, attachment.id)
            logger.info(f"Queued background extraction for attachment {attachment.id}")
        
        return attachment.to_dict()
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except AzureBlobStorageError as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload to blob storage: {str(e)}")
    except Exception as e:
        logger.error(f"Error uploading bill line item attachment: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/view/attachment/{public_id}")
def view_attachment_router(public_id: str, current_user: dict = Depends(get_current_attachment_api)):
    """
    View a file in the browser (displays inline instead of downloading).
    """
    try:
        attachment = service.read_by_public_id(public_id=public_id)
        if not attachment:
            raise HTTPException(status_code=404, detail="Attachment not found")
        
        # Check if archived
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
        raise HTTPException(status_code=500, detail=f"Failed to view from blob storage: {str(e)}")
    except Exception as e:
        logger.error(f"Error viewing attachment: {e}")
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


# ============================================================================
# Extraction Endpoints
# ============================================================================

@router.post("/extract/attachment/{public_id}")
def extract_attachment_router(
    public_id: str,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_attachment_api),
):
    """
    Manually trigger text extraction for an attachment.
    Useful for re-extracting or extracting attachments uploaded before extraction was enabled.
    """
    try:
        attachment = service.read_by_public_id(public_id=public_id)
        if not attachment:
            raise HTTPException(status_code=404, detail="Attachment not found")
        
        if not extraction_service.is_extractable(attachment):
            raise HTTPException(
                status_code=400,
                detail=f"Content type '{attachment.content_type}' is not supported for extraction"
            )
        
        # Mark as pending and trigger background extraction
        extraction_service.mark_as_pending(attachment.id)
        background_tasks.add_task(trigger_extraction_background, attachment.id)
        
        return {
            "message": "Extraction queued",
            "attachment_id": attachment.id,
            "public_id": attachment.public_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error triggering extraction: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/extraction-status/attachment/{public_id}")
def get_extraction_status_router(
    public_id: str,
    current_user: dict = Depends(get_current_attachment_api),
):
    """
    Get the extraction status for an attachment.
    """
    try:
        attachment = service.read_by_public_id(public_id=public_id)
        if not attachment:
            raise HTTPException(status_code=404, detail="Attachment not found")
        
        return {
            "public_id": attachment.public_id,
            "extraction_status": attachment.extraction_status,
            "extracted_datetime": attachment.extracted_datetime,
            "extraction_error": attachment.extraction_error,
            "has_extracted_text": bool(attachment.extracted_text_blob_url),
            "extracted_text_blob_url": attachment.extracted_text_blob_url,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting extraction status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/extracted-text/attachment/{public_id}")
def get_extracted_text_router(
    public_id: str,
    current_user: dict = Depends(get_current_attachment_api),
):
    """
    Get the extracted text content for an attachment.
    Fetches from blob storage where extraction results are stored as JSON.
    """
    try:
        attachment = service.read_by_public_id(public_id=public_id)
        if not attachment:
            raise HTTPException(status_code=404, detail="Attachment not found")
        
        if attachment.extraction_status != "completed":
            raise HTTPException(
                status_code=400,
                detail=f"Extraction not completed. Status: {attachment.extraction_status}"
            )
        
        if not attachment.extracted_text_blob_url:
            raise HTTPException(
                status_code=404,
                detail="Extraction result not found"
            )
        
        # Fetch extracted text from blob storage
        extracted_text = extraction_service.get_extracted_text(attachment)
        if extracted_text is None:
            raise HTTPException(
                status_code=500,
                detail="Failed to retrieve extraction result from storage"
            )
        
        return {
            "public_id": attachment.public_id,
            "filename": attachment.original_filename,
            "extracted_text": extracted_text,
            "extracted_datetime": attachment.extracted_datetime,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting extracted text: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/extraction-result/attachment/{public_id}")
def get_extraction_result_router(
    public_id: str,
    current_user: dict = Depends(get_current_attachment_api),
):
    """
    Get the full extraction result for an attachment.
    Includes content, tables, paragraphs, and key-value pairs.
    """
    try:
        attachment = service.read_by_public_id(public_id=public_id)
        if not attachment:
            raise HTTPException(status_code=404, detail="Attachment not found")
        
        if attachment.extraction_status != "completed":
            raise HTTPException(
                status_code=400,
                detail=f"Extraction not completed. Status: {attachment.extraction_status}"
            )
        
        if not attachment.extracted_text_blob_url:
            raise HTTPException(
                status_code=404,
                detail="Extraction result not found"
            )
        
        # Fetch full extraction result from blob storage
        result = extraction_service.get_extraction_result(attachment)
        if result is None:
            raise HTTPException(
                status_code=500,
                detail="Failed to retrieve extraction result from storage"
            )
        
        return {
            "public_id": attachment.public_id,
            "filename": attachment.original_filename,
            "extracted_datetime": attachment.extracted_datetime,
            "extraction_result": result,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting extraction result: {e}")
        raise HTTPException(status_code=500, detail=str(e))

