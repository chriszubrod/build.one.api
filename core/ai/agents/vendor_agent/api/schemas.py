"""
VendorAgent API Schemas

Pydantic models for request/response validation.
"""
from typing import Optional
from pydantic import BaseModel, Field


# =============================================================================
# Request Schemas
# =============================================================================

class ChatMessageRequest(BaseModel):
    """Request to send a chat message to the agent."""
    message: str = Field(..., min_length=1, max_length=2000, description="User message to the agent")


class ApproveProposalRequest(BaseModel):
    """Request to approve a proposal."""
    pass  # No additional fields needed, user context comes from auth


class RejectProposalRequest(BaseModel):
    """Request to reject a proposal with required explanation."""
    rejection_reason: str = Field(
        ...,
        min_length=10,
        max_length=2000,
        description="Required explanation for why the proposal was rejected"
    )


class ApplyProposalRequest(BaseModel):
    """Request to apply an approved proposal."""
    pass  # No additional fields needed


class RunBatchClassificationRequest(BaseModel):
    """Request to run batch vendor classification."""
    limit: Optional[int] = Field(default=50, ge=1, le=200, description="Maximum vendors to process")


# =============================================================================
# Response Schemas
# =============================================================================

class ProposalFieldResponse(BaseModel):
    """A single field change in a proposal."""
    public_id: str
    field_name: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    old_display_value: Optional[str] = None
    new_display_value: Optional[str] = None
    field_reasoning: Optional[str] = None


class ProposalResponse(BaseModel):
    """A proposal with its field changes."""
    public_id: str
    status: str
    reasoning: str
    confidence: Optional[float] = None
    created_datetime: str
    responded_datetime: Optional[str] = None
    responded_by: Optional[str] = None
    rejection_reason: Optional[str] = None
    applied_datetime: Optional[str] = None
    applied_by: Optional[str] = None
    fields: list[ProposalFieldResponse] = []


class ConversationMessageResponse(BaseModel):
    """A message in the conversation history."""
    public_id: str
    role: str
    content: str
    message_type: Optional[str] = None
    created_datetime: str


class AgentContextResponse(BaseModel):
    """
    Full agent context for a vendor, used by the sidebar chat UI.

    Includes pending proposals and conversation history.
    """
    vendor_public_id: str
    vendor_name: str
    has_vendor_type: bool
    current_vendor_type: Optional[str] = None
    pending_proposals: list[ProposalResponse] = []
    conversation: list[ConversationMessageResponse] = []


class ChatResponse(BaseModel):
    """Response from sending a chat message."""
    success: bool
    response: Optional[str] = None
    proposals_created: int = 0
    error: Optional[str] = None


class ProposalActionResponse(BaseModel):
    """Response from a proposal action (approve/reject/apply)."""
    success: bool
    proposal_public_id: str
    new_status: str
    message: str
    error: Optional[str] = None


class BatchRunResponse(BaseModel):
    """Response from running batch classification."""
    success: bool
    run_public_id: str
    vendors_processed: int = 0
    proposals_created: int = 0
    skipped: int = 0
    error: Optional[str] = None
