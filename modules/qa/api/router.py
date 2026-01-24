# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

# Local Imports
from modules.qa.business.service import get_qa_service
from modules.auth.business.service import get_current_user_api as get_current_qa_api
from integrations.azure.ai.openai_client import AzureOpenAIError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "qa"])


class QuestionRequest(BaseModel):
    """Request body for asking a question."""
    question: str
    category: Optional[str] = None
    max_documents: int = 5
    search_mode: str = "hybrid"


class QuestionResponse(BaseModel):
    """Response for a question."""
    question: str
    answer: str
    sources: list


@router.post("/qa/ask", response_model=QuestionResponse)
def ask_question_router(
    body: QuestionRequest,
    current_user: dict = Depends(get_current_qa_api),
):
    """
    Ask a natural language question about your documents.
    
    The system will:
    1. Search for relevant documents
    2. Use AI to generate an answer based on the documents
    3. Return the answer with source citations
    
    **Examples:**
    - "What invoices did ABC Concrete send us last month?"
    - "Show me all documents related to Project X"
    - "What is the total amount of unpaid bills?"
    """
    try:
        qa_service = get_qa_service()
        
        result = qa_service.ask(
            question=body.question,
            category=body.category,
            max_documents=body.max_documents,
            search_mode=body.search_mode,
        )
        
        return QuestionResponse(
            question=result["question"],
            answer=result["answer"],
            sources=result["sources"],
        )
    
    except AzureOpenAIError as e:
        logger.error(f"Q&A error: {e}")
        raise HTTPException(status_code=500, detail=f"AI service error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected Q&A error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/qa/ask")
def ask_question_get_router(
    q: str = Query(..., description="Your question"),
    category: Optional[str] = Query(None, description="Filter by document category"),
    max_documents: int = Query(5, ge=1, le=10, description="Maximum documents to search"),
    search_mode: str = Query("hybrid", description="Search mode: keyword, semantic, or hybrid"),
    current_user: dict = Depends(get_current_qa_api),
):
    """
    Ask a question using GET request (for easy testing).
    
    Same as POST /qa/ask but with query parameters.
    """
    try:
        qa_service = get_qa_service()
        
        result = qa_service.ask(
            question=q,
            category=category,
            max_documents=max_documents,
            search_mode=search_mode,
        )
        
        return {
            "question": result["question"],
            "answer": result["answer"],
            "sources": result["sources"],
        }
    
    except AzureOpenAIError as e:
        logger.error(f"Q&A error: {e}")
        raise HTTPException(status_code=500, detail=f"AI service error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected Q&A error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/qa/analyze")
def analyze_question_router(
    body: QuestionRequest,
    current_user: dict = Depends(get_current_qa_api),
):
    """
    Analyze a question to understand intent and entities.
    
    Useful for debugging or understanding how the system interprets questions.
    
    Returns:
    - intent: What the user wants (find_documents, summarize, etc.)
    - entities: Key entities mentioned (names, dates, amounts)
    - filters: Suggested search filters
    - search_query: Optimized search query
    """
    try:
        qa_service = get_qa_service()
        
        result = qa_service.analyze_question(body.question)
        
        return {
            "question": body.question,
            "analysis": result,
        }
    
    except AzureOpenAIError as e:
        logger.error(f"Analysis error: {e}")
        raise HTTPException(status_code=500, detail=f"AI service error: {str(e)}")
    except Exception as e:
        logger.error(f"Unexpected analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
