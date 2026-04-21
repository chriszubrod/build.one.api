# Python Standard Library Imports
import io
import logging
import secrets
import time
import uuid
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, Form, status
from fastapi.responses import Response, StreamingResponse

# Local Imports
from entities.attachment.api.schemas import AttachmentCreate, AttachmentUpdate
from entities.attachment.business.service import AttachmentService
from shared.api.responses import list_response, item_response, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from shared.storage import AzureBlobStorage, AzureBlobStorageError
from shared.pdf_utils import compact_pdf
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "attachment"])
service = AttachmentService()


@router.post("/create/attachment")
def create_attachment_router(body: AttachmentCreate, current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS, "can_create"))):
    """
    Create a new attachment (metadata only, upload handled separately).
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
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
    
    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create attachment")
        )

    return item_response(result.get("data"))


@router.get("/get/attachments")
def get_attachments_router(
    category: Optional[str] = None,
    is_archived: Optional[bool] = None,
    status: Optional[str] = None,
    current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS)),
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
        
        return list_response([attachment.to_dict() for attachment in attachments])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/attachment/{public_id}")
def get_attachment_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS))):
    """
    Read an attachment by public ID.
    """
    try:
        attachment = service.read_by_public_id(public_id=public_id)
        if not attachment:
            raise_not_found("Attachment")
        return item_response(attachment.to_dict())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/attachment/id/{id}")
def get_attachment_by_id_router(id: int, current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS))):
    """
    Read an attachment by ID.
    """
    try:
        attachment = service.read_by_id(id=id)
        if not attachment:
            raise_not_found("Attachment")
        return item_response(attachment.to_dict())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/attachments/by-category/{category}")
def get_attachments_by_category_router(category: str, current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS))):
    """
    List attachments by category.
    """
    try:
        attachments = service.read_by_category(category)
        return list_response([attachment.to_dict() for attachment in attachments])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/attachments/by-hash/{hash}")
def get_attachment_by_hash_router(hash: str, current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS))):
    """
    Find duplicate attachments by hash.
    """
    try:
        attachment = service.read_by_hash(hash)
        if not attachment:
            raise_not_found("Attachment")
        return item_response(attachment.to_dict())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/update/attachment/{public_id}")
def update_attachment_by_public_id_router(
    public_id: str, body: AttachmentUpdate, current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS, "can_update"))
):
    """
    Update an attachment by public ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
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
    
    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update attachment")
        )

    return item_response(result.get("data"))


@router.put("/archive/attachment/{public_id}")
def archive_attachment_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS, "can_update"))):
    """
    Archive an attachment (soft delete).
    """
    try:
        attachment = service.archive(public_id=public_id)
        if not attachment:
            raise_not_found("Attachment")
        return item_response(attachment.to_dict())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/unarchive/attachment/{public_id}")
def unarchive_attachment_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS, "can_update"))):
    """
    Unarchive an attachment.
    """
    try:
        attachment = service.unarchive(public_id=public_id)
        if not attachment:
            raise_not_found("Attachment")
        return item_response(attachment.to_dict())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete/attachment/{public_id}")
def delete_attachment_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS, "can_delete"))):
    """
    Delete an attachment by public ID (and blob).
    """
    try:
        attachment = service.read_by_public_id(public_id=public_id)
        if not attachment:
            raise_not_found("Attachment")

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
        return item_response(deleted.to_dict() if deleted else {})
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
    current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS, "can_create")),
):
    """
    Upload a file to blob storage and create attachment record.
    Text extraction runs in background for supported file types.
    """
    try:
        # Read file content
        file_content = await file.read()
        file_size = len(file_content)

        # Validate file size (before compaction)
        service.validate_file_size(file_size)

        # Compact PDFs to reduce stored size (lossless when possible)
        content_type = file.content_type or "application/octet-stream"
        if content_type == "application/pdf":
            file_content = compact_pdf(file_content)
            file_size = len(file_content)

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
            content_type=content_type,
        )

        # Create attachment record
        attachment = service.create(
            filename=file.filename,
            original_filename=file.filename,
            file_extension=file_extension,
            content_type=content_type,
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

        return item_response(attachment.to_dict())
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
    current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS, "can_create")),
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

        # Validate file size (before compaction)
        service.validate_file_size(file_size)

        # Compact PDFs to reduce stored size (lossless when possible)
        content_type = file.content_type or "application/octet-stream"
        if content_type == "application/pdf":
            file_content = compact_pdf(file_content)
            file_size = len(file_content)

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
            content_type=content_type,
        )
        logger.info(f"Uploaded bill line item attachment to Azure Blob: {blob_url}")

        # Create attachment record in database
        attachment = service.create(
            filename=file.filename,
            original_filename=file.filename,
            file_extension=file_extension,
            content_type=content_type,
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

        return item_response(attachment.to_dict())

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except AzureBlobStorageError as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload to blob storage: {str(e)}")
    except Exception as e:
        logger.error(f"Error uploading bill line item attachment: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/view/attachment/{public_id}")
def view_attachment_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS))):
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
        
        media_type = metadata.get("content_type") or attachment.content_type or "application/octet-stream"
        if media_type == "application/octet-stream":
            fn = (attachment.original_filename or attachment.filename or "").lower()
            ext = (attachment.file_extension or "").lower()
            if ext == "pdf" or fn.endswith(".pdf"):
                media_type = "application/pdf"
        filename = (attachment.original_filename or attachment.filename or "attachment").replace('"', "'")
        return StreamingResponse(
            io.BytesIO(file_content),
            media_type=media_type,
            headers={
                "Content-Disposition": f'inline; filename="{filename}"',
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
def download_attachment_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS))):
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


# -----------------------------------------------------------------------------
# Temporary pending bill file (for list -> create page transfer when file too large for sessionStorage)
# -----------------------------------------------------------------------------
_PENDING_FILE_STORE: dict[str, dict] = {}
_PENDING_FILE_TTL_SEC = 300  # 5 minutes


def _clean_expired_pending_files():
    now = time.time()
    expired = [k for k, v in _PENDING_FILE_STORE.items() if v.get("expires_at", 0) < now]
    for k in expired:
        del _PENDING_FILE_STORE[k]


@router.post("/temp/pending-bill-file")
async def upload_temp_pending_bill_file(
    file: UploadFile = File(...),
    current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS, "can_create")),
):
    """
    Store a file temporarily for transfer to the bill create page.
    Returns a token to pass in the URL; the create page fetches the file by token.
    Files are deleted after retrieval or after TTL (5 minutes).
    """
    try:
        content = await file.read()
        _clean_expired_pending_files()
        token = secrets.token_urlsafe(32)
        _PENDING_FILE_STORE[token] = {
            "filename": file.filename or "attachment",
            "content_type": file.content_type or "application/octet-stream",
            "bytes": content,
            "expires_at": time.time() + _PENDING_FILE_TTL_SEC,
        }
        return item_response({"token": token})
    except Exception as e:
        logger.error(f"Temp pending file upload failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/temp/pending-bill-file")
async def get_temp_pending_bill_file(
    token: str,
    current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS)),
):
    """
    Retrieve a temporarily stored file by token. File is removed after retrieval.
    Used by the bill create page when navigating with pendingFileToken in the URL.
    """
    _clean_expired_pending_files()
    entry = _PENDING_FILE_STORE.pop(token, None)
    if not entry:
        raise_not_found("File")
    return Response(
        content=entry["bytes"],
        media_type=entry["content_type"],
        headers={
            "Content-Disposition": f'inline; filename="{entry["filename"]}"',
        },
    )


@router.post("/temp/pending-expense-file")
async def upload_temp_pending_expense_file(
    file: UploadFile = File(...),
    current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS, "can_create")),
):
    """
    Store a file temporarily for transfer to the expense create page.
    Returns a token to pass in the URL; the create page fetches the file by token.
    Files are deleted after retrieval or after TTL (5 minutes).
    """
    try:
        content = await file.read()
        _clean_expired_pending_files()
        token = secrets.token_urlsafe(32)
        _PENDING_FILE_STORE[token] = {
            "filename": file.filename or "attachment",
            "content_type": file.content_type or "application/octet-stream",
            "bytes": content,
            "expires_at": time.time() + _PENDING_FILE_TTL_SEC,
        }
        return item_response({"token": token})
    except Exception as e:
        logger.error(f"Temp pending expense file upload failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/temp/pending-expense-file")
async def get_temp_pending_expense_file(
    token: str,
    current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS)),
):
    """
    Retrieve a temporarily stored file by token. File is removed after retrieval.
    Used by the expense create page when navigating with pendingFileToken in the URL.
    """
    _clean_expired_pending_files()
    entry = _PENDING_FILE_STORE.pop(token, None)
    if not entry:
        raise_not_found("File")
    return Response(
        content=entry["bytes"],
        media_type=entry["content_type"],
        headers={
            "Content-Disposition": f'inline; filename="{entry["filename"]}"',
        },
    )

