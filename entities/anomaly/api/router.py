# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

# Local Imports
from services.anomaly.business.service import get_anomaly_service
from services.auth.business.service import get_current_user_api as get_current_anomaly_api

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "anomaly"])


class AnomalyCheckResponse(BaseModel):
    """Response for anomaly check."""
    has_anomaly: bool
    anomaly_type: Optional[str]
    severity: Optional[str]
    blocked: bool
    flagged: bool
    notification_required: bool
    message: Optional[str]
    details: dict
    related_documents: list


class PreUploadCheckRequest(BaseModel):
    """Request for pre-upload duplicate check."""
    file_hash: str
    extracted_text: Optional[str] = None
    category: Optional[str] = None


@router.get("/anomaly/check/{public_id}", response_model=AnomalyCheckResponse)
def check_attachment_anomaly_router(
    public_id: str,
    check_category_only: bool = Query(True, description="Only compare against same category"),
    current_user: dict = Depends(get_current_anomaly_api),
):
    """
    Check an attachment for anomalies.
    
    Detects:
    - Exact duplicates (same file hash)
    - Near duplicates (95%+ semantic similarity)
    - Similar content (85%+ semantic similarity)
    
    Returns anomaly details including:
    - Whether to block, flag, or notify
    - Related documents that triggered the anomaly
    """
    try:
        anomaly_service = get_anomaly_service()
        
        result = anomaly_service.check_attachment_by_public_id(
            public_id=public_id,
            check_category_only=check_category_only,
        )
        
        if result is None:
            raise HTTPException(status_code=404, detail="Attachment not found")
        
        return AnomalyCheckResponse(**result.to_dict())
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Anomaly check error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/anomaly/pre-upload-check", response_model=AnomalyCheckResponse)
def pre_upload_check_router(
    body: PreUploadCheckRequest,
    current_user: dict = Depends(get_current_anomaly_api),
):
    """
    Check for duplicates before uploading a file.
    
    Useful for validating a file before upload to prevent duplicates.
    
    Provide:
    - file_hash: SHA-256 hash of the file
    - extracted_text: (optional) Text content for semantic comparison
    - category: (optional) Category to filter comparisons
    """
    try:
        anomaly_service = get_anomaly_service()
        
        result = anomaly_service.check_for_duplicates_before_upload(
            file_hash=body.file_hash,
            extracted_text=body.extracted_text,
            category=body.category,
        )
        
        return AnomalyCheckResponse(**result.to_dict())
    
    except Exception as e:
        logger.error(f"Pre-upload check error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/anomaly/scan-all")
def scan_all_attachments_router(
    category: Optional[str] = Query(None, description="Filter by category"),
    limit: int = Query(50, ge=1, le=200, description="Maximum attachments to scan"),
    current_user: dict = Depends(get_current_anomaly_api),
):
    """
    Scan multiple attachments for anomalies.
    
    Returns a summary of all detected anomalies across attachments.
    Useful for batch auditing.
    """
    try:
        from services.attachment.business.service import AttachmentService
        
        anomaly_service = get_anomaly_service()
        attachment_service = AttachmentService()
        
        # Get attachments to scan
        if category:
            attachments = attachment_service.read_by_category(category)
        else:
            attachments = attachment_service.read_all()
        
        # Limit the number to scan
        attachments = attachments[:limit]
        
        # Scan each attachment
        results = []
        anomaly_count = 0
        
        for attachment in attachments:
            try:
                result = anomaly_service.check_attachment(
                    attachment,
                    check_category_only=True,
                )
                
                if result.has_anomaly:
                    anomaly_count += 1
                    results.append({
                        "public_id": str(attachment.public_id),
                        "filename": attachment.original_filename or attachment.filename,
                        "anomaly": result.to_dict(),
                    })
            except Exception as e:
                logger.error(f"Error scanning attachment {attachment.id}: {e}")
        
        return {
            "scanned_count": len(attachments),
            "anomaly_count": anomaly_count,
            "anomalies": results,
        }
    
    except Exception as e:
        logger.error(f"Scan error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
