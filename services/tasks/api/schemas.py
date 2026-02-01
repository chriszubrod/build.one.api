# Python Standard Library Imports
from typing import Any, Dict, List, Optional

# Third-Party Imports
from pydantic import BaseModel, Field


class StartWorkflowRequest(BaseModel):
    """Request to start a workflow from email (tasks browse)."""
    message_id: str = Field(..., description="Selected message ID")
    conversation_id: Optional[str] = Field(default=None, description="Conversation ID")
    conversation: List[Dict[str, Any]] = Field(default_factory=list, description="Full conversation messages")
    total_attachments: int = Field(default=0, description="Total attachments in conversation")


class PollRunResponse(BaseModel):
    """Response from poll/run."""
    new_workflows: int = 0
    replies_processed: int = 0
    reminders_sent: int = 0
