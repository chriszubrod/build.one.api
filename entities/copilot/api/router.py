# Python Standard Library Imports
import logging
from typing import Optional, List

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

# Local Imports
from entities.copilot.business.service import get_copilot_service
from entities.auth.business.service import get_current_user_api as get_current_copilot_api

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["api", "copilot"])


class ChatRequest(BaseModel):
    """Request for chat endpoint."""
    message: str
    conversation_id: Optional[str] = None


class ChatResponse(BaseModel):
    """Response from chat endpoint."""
    message: str
    conversation_id: str
    intent: Optional[str]
    action_taken: Optional[str]
    data: Optional[dict]
    suggestions: List[str]
    sources: List[dict]
    requires_confirmation: bool


class ConversationResponse(BaseModel):
    """Response for conversation details."""
    id: str
    messages: List[dict]
    created_at: str
    updated_at: str


@router.post("/copilot/chat", response_model=ChatResponse)
def chat_router(
    body: ChatRequest,
    current_user: dict = Depends(get_current_copilot_api),
):
    """
    Send a message to the AI Copilot.
    
    The copilot can:
    - Answer questions about documents and data
    - Search for documents
    - Categorize documents
    - Check for duplicates
    - Provide status updates
    
    Include conversation_id to continue an existing conversation.
    """
    try:
        copilot_service = get_copilot_service()
        
        # Add user context
        context = {
            "user_id": current_user.get("id"),
            "user_name": current_user.get("name"),
        }
        
        response = copilot_service.chat(
            message=body.message,
            conversation_id=body.conversation_id,
            context=context,
        )
        
        # Get conversation ID
        if body.conversation_id:
            conversation = copilot_service.get_conversation(body.conversation_id)
            conv_id = conversation.id if conversation else body.conversation_id
        else:
            # Get the newly created conversation
            conversations = copilot_service._conversations
            conv_id = list(conversations.keys())[-1] if conversations else ""
        
        return ChatResponse(
            message=response.message,
            conversation_id=conv_id,
            intent=response.intent.value if response.intent else None,
            action_taken=response.action_taken,
            data=response.data,
            suggestions=response.suggestions,
            sources=response.sources,
            requires_confirmation=response.requires_confirmation,
        )
    
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/copilot/conversations/{conversation_id}", response_model=ConversationResponse)
def get_conversation_router(
    conversation_id: str,
    current_user: dict = Depends(get_current_copilot_api),
):
    """
    Get a conversation by ID.
    
    Returns the full conversation history.
    """
    try:
        copilot_service = get_copilot_service()
        
        conversation = copilot_service.get_conversation(conversation_id)
        
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        return ConversationResponse(
            id=conversation.id,
            messages=[msg.to_dict() for msg in conversation.messages],
            created_at=conversation.created_at.isoformat(),
            updated_at=conversation.updated_at.isoformat(),
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get conversation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/copilot/conversations/{conversation_id}")
def delete_conversation_router(
    conversation_id: str,
    current_user: dict = Depends(get_current_copilot_api),
):
    """
    Delete a conversation.
    
    Removes the conversation from memory.
    """
    try:
        copilot_service = get_copilot_service()
        
        if conversation_id in copilot_service._conversations:
            del copilot_service._conversations[conversation_id]
            return {"success": True, "message": "Conversation deleted"}
        else:
            raise HTTPException(status_code=404, detail="Conversation not found")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete conversation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/copilot/quick-actions")
def get_quick_actions_router(
    current_user: dict = Depends(get_current_copilot_api),
):
    """
    Get available quick actions for the copilot.
    
    Returns a list of suggested prompts users can click.
    """
    return {
        "actions": [
            {
                "label": "Check System Status",
                "prompt": "What's the current system status?",
                "icon": "status",
            },
            {
                "label": "Pending Documents",
                "prompt": "Show me documents that need review",
                "icon": "documents",
            },
            {
                "label": "Recent Bills",
                "prompt": "Find bills from the last 7 days",
                "icon": "bill",
            },
            {
                "label": "Search Documents",
                "prompt": "Search for ",
                "icon": "search",
                "requires_input": True,
            },
            {
                "label": "Help",
                "prompt": "What can you help me with?",
                "icon": "help",
            },
        ]
    }
