# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, Query

# Local Imports
from entities.search.business.service import get_search_service
from entities.auth.business.service import get_current_user_api as get_current_search_api
from integrations.azure.ai.search_client import AzureSearchError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "search"])


@router.get("/search/documents")
def search_documents_router(
    q: str = Query(..., description="Search query"),
    mode: str = Query("hybrid", description="Search mode: keyword, semantic, or hybrid"),
    category: Optional[str] = Query(None, description="Filter by category"),
    content_type: Optional[str] = Query(None, description="Filter by content type"),
    top: int = Query(10, ge=1, le=50, description="Maximum results to return"),
    current_user: dict = Depends(get_current_search_api),
):
    """
    Search documents using keyword, semantic, or hybrid search.
    
    - **keyword**: Traditional full-text search
    - **semantic**: Vector similarity search (finds conceptually similar content)
    - **hybrid**: Combines keyword and semantic for best results (recommended)
    """
    try:
        search_service = get_search_service()
        
        if mode == "keyword":
            results = search_service.search(
                query=q,
                category=category,
                content_type=content_type,
                top=top,
            )
        elif mode == "semantic":
            results = search_service.semantic_search(
                query=q,
                category=category,
                content_type=content_type,
                top=top,
            )
        elif mode == "hybrid":
            results = search_service.hybrid_search(
                query=q,
                category=category,
                content_type=content_type,
                top=top,
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid search mode: {mode}. Use 'keyword', 'semantic', or 'hybrid'"
            )
        
        return {
            "query": q,
            "mode": mode,
            "count": len(results),
            "results": results,
        }
    
    except AzureSearchError as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search/index/{public_id}")
def index_document_router(
    public_id: str,
    current_user: dict = Depends(get_current_search_api),
):
    """
    Manually index a document in Azure AI Search.
    
    Useful for re-indexing after extraction or for documents uploaded
    before search indexing was enabled.
    """
    try:
        from entities.attachment.business.service import AttachmentService
        
        attachment_service = AttachmentService()
        search_service = get_search_service()
        
        # Get the attachment
        attachment = attachment_service.read_by_public_id(public_id)
        if not attachment:
            raise HTTPException(status_code=404, detail="Attachment not found")
        
        if attachment.extraction_status != "completed":
            raise HTTPException(
                status_code=400,
                detail=f"Attachment extraction not completed. Status: {attachment.extraction_status}"
            )
        
        # Index the document
        success = search_service.index_attachment(attachment)
        
        return {
            "message": "Document indexed" if success else "Indexing may have partial failures",
            "public_id": public_id,
            "success": success,
        }
    
    except HTTPException:
        raise
    except AzureSearchError as e:
        logger.error(f"Index error: {e}")
        raise HTTPException(status_code=500, detail=f"Indexing failed: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected index error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/search/index/{public_id}")
def remove_from_index_router(
    public_id: str,
    current_user: dict = Depends(get_current_search_api),
):
    """
    Remove a document from the search index.
    """
    try:
        search_service = get_search_service()
        success = search_service.remove_from_index(public_id)
        
        return {
            "message": "Document removed from index" if success else "Removal failed",
            "public_id": public_id,
            "success": success,
        }
    
    except AzureSearchError as e:
        logger.error(f"Remove from index error: {e}")
        raise HTTPException(status_code=500, detail=f"Removal failed: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
