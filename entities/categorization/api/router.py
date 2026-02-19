# Python Standard Library Imports
import json
import logging
from typing import Optional, List

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

# Local Imports
from entities.categorization.business.service import get_categorization_service
from entities.categorization.business.model import DocumentCategory
from entities.attachment.persistence.repo import AttachmentRepository
from entities.auth.business.service import get_current_user_api as get_current_categorization_api

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "categorization"])


class CategorizationResponse(BaseModel):
    """Response for categorization result."""
    category: str
    confidence: float
    status: str
    reasoning: Optional[str]
    extracted_fields: Optional[dict]
    alternative_categories: list
    suggested_actions: Optional[dict] = None


class CategorizationSaveResponse(BaseModel):
    """Response after saving categorization."""
    success: bool
    public_id: str
    category: str
    status: str
    message: str


class ConfirmCategoryRequest(BaseModel):
    """Request to confirm or reject categorization."""
    confirmed: bool
    manual_category: Optional[str] = None


class CategorizeTextRequest(BaseModel):
    """Request to categorize raw text."""
    text: str
    filename: Optional[str] = None


@router.get("/categorization/categories")
def list_categories_router(current_user: dict = Depends(get_current_categorization_api)):
    """
    List all available document categories.
    """
    return {
        "categories": [
            {"value": cat.value, "name": cat.name.replace("_", " ").title()}
            for cat in DocumentCategory
        ]
    }


@router.post("/categorization/categorize/{public_id}", response_model=CategorizationResponse)
def categorize_attachment_router(
    public_id: str,
    save: bool = Query(True, description="Save result to database"),
    current_user: dict = Depends(get_current_categorization_api),
):
    """
    Categorize an attachment using AI.
    
    Analyzes the extracted text and determines the document category
    with confidence score and extracted fields.
    """
    try:
        categorization_service = get_categorization_service()
        
        result = categorization_service.categorize_attachment_by_public_id(public_id)
        
        if result is None:
            raise HTTPException(
                status_code=400,
                detail="Cannot categorize: attachment not found or not extracted"
            )
        
        # Get suggested actions for this category
        actions = categorization_service.get_category_actions(result.category)
        
        # Save to database if requested
        if save:
            repo = AttachmentRepository()
            from entities.attachment.business.service import AttachmentService
            attachment_service = AttachmentService()
            
            attachment = attachment_service.read_by_public_id(public_id)
            if attachment:
                extracted_fields_json = None
                if result.extracted_fields:
                    extracted_fields_json = json.dumps(result.extracted_fields.to_dict())
                
                repo.update_categorization(
                    id=attachment.id,
                    ai_category=result.category.value,
                    ai_category_confidence=result.confidence,
                    ai_category_status=result.status.value,
                    ai_category_reasoning=result.reasoning,
                    ai_extracted_fields=extracted_fields_json,
                )
        
        return CategorizationResponse(
            category=result.category.value,
            confidence=result.confidence,
            status=result.status.value,
            reasoning=result.reasoning,
            extracted_fields=result.extracted_fields.to_dict() if result.extracted_fields else None,
            alternative_categories=result.alternative_categories,
            suggested_actions=actions,
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Categorization error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/categorization/categorize-text", response_model=CategorizationResponse)
def categorize_text_router(
    body: CategorizeTextRequest,
    current_user: dict = Depends(get_current_categorization_api),
):
    """
    Categorize raw text (without an attachment).
    
    Useful for testing or pre-upload categorization.
    """
    try:
        categorization_service = get_categorization_service()
        
        result = categorization_service.categorize_text(body.text, body.filename)
        
        if result is None:
            raise HTTPException(status_code=400, detail="Categorization failed")
        
        actions = categorization_service.get_category_actions(result.category)
        
        return CategorizationResponse(
            category=result.category.value,
            confidence=result.confidence,
            status=result.status.value,
            reasoning=result.reasoning,
            extracted_fields=result.extracted_fields.to_dict() if result.extracted_fields else None,
            alternative_categories=result.alternative_categories,
            suggested_actions=actions,
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Text categorization error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/categorization/confirm/{public_id}", response_model=CategorizationSaveResponse)
def confirm_categorization_router(
    public_id: str,
    body: ConfirmCategoryRequest,
    current_user: dict = Depends(get_current_categorization_api),
):
    """
    Confirm or reject AI categorization.
    
    If confirmed=true, marks the AI suggestion as confirmed.
    If confirmed=false, optionally provides a manual_category to override.
    """
    try:
        from entities.attachment.business.service import AttachmentService
        
        attachment_service = AttachmentService()
        repo = AttachmentRepository()
        
        attachment = attachment_service.read_by_public_id(public_id)
        if not attachment:
            raise HTTPException(status_code=404, detail="Attachment not found")
        
        # Validate manual category if provided
        if body.manual_category:
            try:
                DocumentCategory(body.manual_category)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid category: {body.manual_category}"
                )
        
        result = repo.confirm_categorization(
            id=attachment.id,
            confirmed=body.confirmed,
            manual_category=body.manual_category,
        )
        
        if not result:
            raise HTTPException(status_code=500, detail="Failed to update categorization")
        
        return CategorizationSaveResponse(
            success=True,
            public_id=public_id,
            category=result.ai_category or "",
            status=result.ai_category_status or "",
            message="Categorization confirmed" if body.confirmed else "Category updated manually",
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Confirm categorization error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/categorization/pending")
def get_pending_categorization_router(
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_categorization_api),
):
    """
    Get attachments pending categorization.
    
    Returns attachments that are extracted but not yet categorized.
    """
    try:
        repo = AttachmentRepository()
        
        attachments = repo.read_pending_categorization(limit=limit)
        
        return {
            "count": len(attachments),
            "attachments": [
                {
                    "public_id": str(a.public_id),
                    "filename": a.original_filename or a.filename,
                    "content_type": a.content_type,
                    "category": a.category,
                    "extraction_status": a.extraction_status,
                }
                for a in attachments
            ],
        }
    
    except Exception as e:
        logger.error(f"Get pending categorization error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/categorization/batch")
def batch_categorize_router(
    limit: int = Query(10, ge=1, le=50),
    current_user: dict = Depends(get_current_categorization_api),
):
    """
    Batch categorize pending attachments.
    
    Processes up to `limit` uncategorized attachments.
    """
    try:
        repo = AttachmentRepository()
        categorization_service = get_categorization_service()
        
        attachments = repo.read_pending_categorization(limit=limit)
        
        results = []
        for attachment in attachments:
            try:
                result = categorization_service.categorize_attachment(attachment)
                
                if result:
                    # Save to database
                    extracted_fields_json = None
                    if result.extracted_fields:
                        extracted_fields_json = json.dumps(result.extracted_fields.to_dict())
                    
                    repo.update_categorization(
                        id=attachment.id,
                        ai_category=result.category.value,
                        ai_category_confidence=result.confidence,
                        ai_category_status=result.status.value,
                        ai_category_reasoning=result.reasoning,
                        ai_extracted_fields=extracted_fields_json,
                    )
                    
                    results.append({
                        "public_id": str(attachment.public_id),
                        "filename": attachment.original_filename or attachment.filename,
                        "category": result.category.value,
                        "confidence": result.confidence,
                        "status": result.status.value,
                    })
                else:
                    results.append({
                        "public_id": str(attachment.public_id),
                        "filename": attachment.original_filename or attachment.filename,
                        "error": "Categorization failed",
                    })
            except Exception as e:
                logger.error(f"Error categorizing {attachment.id}: {e}")
                results.append({
                    "public_id": str(attachment.public_id),
                    "filename": attachment.original_filename or attachment.filename,
                    "error": str(e),
                })
        
        return {
            "processed": len(results),
            "results": results,
        }
    
    except Exception as e:
        logger.error(f"Batch categorization error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
