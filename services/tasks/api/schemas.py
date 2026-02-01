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


class TaskCreate(BaseModel):
    """Request to create a new task (manual entry)."""
    title: str = Field(..., description="Task title")
    description: Optional[str] = Field(default=None, description="Task description")
    task_type: str = Field(default="manual", description="Task type (manual, workflow, data_upload)")
    source_type: Optional[str] = Field(default=None, description="Source type (email, upload, manual)")
    context: Optional[Dict[str, Any]] = Field(default=None, description="Type-specific context data")


class TaskUpdate(BaseModel):
    """Request to update a task."""
    title: Optional[str] = Field(default=None, description="Task title")
    description: Optional[str] = Field(default=None, description="Task description")
    status: Optional[str] = Field(default=None, description="Task status")
    context: Optional[Dict[str, Any]] = Field(default=None, description="Type-specific context data")


class TaskStatusUpdate(BaseModel):
    """Request to update only task status."""
    status: str = Field(..., description="New task status")
