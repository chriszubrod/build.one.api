# Python Standard Library Imports
from typing import Optional

# Third-Party Imports
from pydantic import BaseModel, Field


class CancelRequest(BaseModel):
    """Request schema for cancelling a workflow."""
    reason: Optional[str] = Field(
        default=None,
        description="Optional reason for cancellation",
        max_length=500,
    )


class ApproveRequest(BaseModel):
    """Request schema for approving a workflow."""
    project_id: int = Field(
        ...,
        description="Project ID to assign to the workflow",
        gt=0,
    )
    cost_code: str = Field(
        ...,
        description="Cost code for the workflow",
        min_length=1,
        max_length=50,
    )


class RejectRequest(BaseModel):
    """Request schema for rejecting a workflow."""
    reason: str = Field(
        ...,
        description="Reason for rejection",
        min_length=1,
        max_length=500,
    )


class ActionResponse(BaseModel):
    """Response schema for workflow actions."""
    success: bool
    state: str
    message: str
