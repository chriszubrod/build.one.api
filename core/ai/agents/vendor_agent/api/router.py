"""
VendorAgent API Router

FastAPI endpoints for interacting with the VendorAgent.
"""
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from core.ai.agents.vendor_agent.api.schemas import (
    ChatMessageRequest,
    ApproveProposalRequest,
    RejectProposalRequest,
    ApplyProposalRequest,
    RunBatchClassificationRequest,
    AgentContextResponse,
    ChatResponse,
    ProposalActionResponse,
    ProposalResponse,
    ProposalFieldResponse,
    ConversationMessageResponse,
    BatchRunResponse,
)
from core.ai.agents.vendor_agent.business.service import VendorAgentService
from core.ai.agents.vendor_agent.graph.agent import (
    run_single_vendor_classification,
    run_batch_classification,
)
from entities.vendor.business.service import VendorService
from entities.vendor_type.business.service import VendorTypeService
from entities.auth.business.service import get_current_user_api

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["vendor-agent"])


# =============================================================================
# Helper Functions
# =============================================================================

def get_user_info(current_user: dict) -> tuple[int, str, str]:
    """Extract tenant_id, user_id, and user email from current user."""
    tenant_id = current_user.get("tenant_id", 1)
    user_id = current_user.get("id")
    user_email = current_user.get("email", "unknown")
    return tenant_id, user_id, user_email


def proposal_to_response(proposal, include_fields: bool = True) -> ProposalResponse:
    """Convert a VendorAgentProposal to ProposalResponse."""
    fields = []
    if include_fields and proposal.fields:
        for f in proposal.fields:
            fields.append(ProposalFieldResponse(
                public_id=f.public_id,
                field_name=f.field_name,
                old_value=f.old_value,
                new_value=f.new_value,
                old_display_value=f.old_display_value,
                new_display_value=f.new_display_value,
                field_reasoning=f.field_reasoning,
            ))

    return ProposalResponse(
        public_id=proposal.public_id,
        status=proposal.status,
        reasoning=proposal.reasoning,
        confidence=float(proposal.confidence) if proposal.confidence else None,
        created_datetime=proposal.created_datetime,
        responded_datetime=proposal.responded_datetime,
        responded_by=proposal.responded_by,
        rejection_reason=proposal.rejection_reason,
        applied_datetime=proposal.applied_datetime,
        applied_by=proposal.applied_by,
        fields=fields,
    )


def conversation_to_response(msg) -> ConversationMessageResponse:
    """Convert a VendorAgentConversation to ConversationMessageResponse."""
    return ConversationMessageResponse(
        public_id=msg.public_id,
        role=msg.role,
        content=msg.content,
        message_type=msg.message_type,
        created_datetime=msg.created_datetime,
    )


# =============================================================================
# Agent Context Endpoint
# =============================================================================

@router.get(
    "/vendor/{vendor_public_id}/agent-context",
    response_model=AgentContextResponse,
    summary="Get agent context for a vendor",
    description="Returns pending proposals and conversation history for the sidebar chat UI.",
)
async def get_agent_context(
    vendor_public_id: str,
    current_user: Annotated[dict, Depends(get_current_user_api)],
):
    """
    Get the agent context for a vendor.

    Used by the sidebar chat to display:
    - Pending proposals awaiting approval
    - Conversation history with the agent
    """
    tenant_id, user_id, _ = get_user_info(current_user)

    # Get vendor
    vendor_service = VendorService()
    vendor = vendor_service.read_by_public_id(public_id=vendor_public_id)
    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Vendor not found: {vendor_public_id}",
        )

    # Get current vendor type
    current_type_name = None
    if vendor.vendor_type_id:
        vt_service = VendorTypeService()
        vt = vt_service.read_by_id(id=vendor.vendor_type_id)
        if vt:
            current_type_name = vt.name

    # Get pending proposals
    agent_service = VendorAgentService()
    pending = agent_service.get_proposals_for_vendor(
        vendor_id=vendor.id,
        status="pending",
        include_fields=True,
    )
    pending_responses = [proposal_to_response(p) for p in pending]

    # Get conversation history
    conversation = agent_service.get_conversation(vendor_id=vendor.id, limit=50)
    conversation_responses = [conversation_to_response(m) for m in conversation]

    return AgentContextResponse(
        vendor_public_id=vendor.public_id,
        vendor_name=vendor.name,
        has_vendor_type=vendor.vendor_type_id is not None,
        current_vendor_type=current_type_name,
        pending_proposals=pending_responses,
        conversation=conversation_responses,
    )


# =============================================================================
# Chat Endpoint
# =============================================================================

@router.post(
    "/vendor/{vendor_public_id}/agent/chat",
    response_model=ChatResponse,
    summary="Send a message to the agent",
    description="Send a chat message and get the agent's response.",
)
async def chat_with_agent(
    vendor_public_id: str,
    request: ChatMessageRequest,
    current_user: Annotated[dict, Depends(get_current_user_api)],
):
    """
    Send a message to the VendorAgent for a specific vendor.

    The agent will analyze the vendor and respond, potentially creating
    proposals for VendorType assignment.
    """
    tenant_id, user_id, user_email = get_user_info(current_user)

    # Get vendor
    vendor_service = VendorService()
    vendor = vendor_service.read_by_public_id(public_id=vendor_public_id)
    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Vendor not found: {vendor_public_id}",
        )
    print("DEBUG chat_with_agent: vendor resolved, calling run_single_vendor_classification")

    # Run the agent (vendor resolved from public_id above; use vendor.id only downstream)
    result = run_single_vendor_classification(
        tenant_id=tenant_id,
        vendor_id=vendor.id,
        user_id=user_email,
        user_message=request.message,
    )

    print("DEBUG chat_with_agent: result keys =", list(result.keys()))
    resp_preview = (result.get("response") or "")[:200]
    print("DEBUG chat_with_agent: response (first 200 chars) =", resp_preview)

    return ChatResponse(
        success=result.get("success", False),
        response=result.get("response"),
        proposals_created=result.get("proposals_created", 0),
        error=result.get("error"),
    )


# =============================================================================
# Proposal Action Endpoints
# =============================================================================

@router.post(
    "/vendor/{vendor_public_id}/agent/proposal/{proposal_public_id}/approve",
    response_model=ProposalActionResponse,
    summary="Approve a proposal",
    description="Approve a pending proposal.",
)
async def approve_proposal(
    vendor_public_id: str,
    proposal_public_id: str,
    request: ApproveProposalRequest,
    current_user: Annotated[dict, Depends(get_current_user_api)],
):
    """
    Approve a pending proposal.

    After approval, the proposal can be applied to update the vendor.
    """
    tenant_id, user_id, user_email = get_user_info(current_user)

    agent_service = VendorAgentService()

    # Get the proposal
    proposal = agent_service.get_proposal(public_id=proposal_public_id)
    if not proposal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Proposal not found: {proposal_public_id}",
        )

    if not proposal.is_pending:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Proposal is not pending (status: {proposal.status})",
        )

    # Approve the proposal
    updated = agent_service.approve_proposal(
        public_id=proposal_public_id,
        approved_by=user_email,
    )

    return ProposalActionResponse(
        success=True,
        proposal_public_id=proposal_public_id,
        new_status="approved",
        message="Proposal approved successfully",
    )


@router.post(
    "/vendor/{vendor_public_id}/agent/proposal/{proposal_public_id}/reject",
    response_model=ProposalActionResponse,
    summary="Reject a proposal",
    description="Reject a pending proposal with a required explanation.",
)
async def reject_proposal(
    vendor_public_id: str,
    proposal_public_id: str,
    request: RejectProposalRequest,
    current_user: Annotated[dict, Depends(get_current_user_api)],
):
    """
    Reject a pending proposal.

    The rejection reason is required and will be stored for the agent
    to learn from in future proposals.
    """
    tenant_id, user_id, user_email = get_user_info(current_user)

    agent_service = VendorAgentService()

    # Get the proposal
    proposal = agent_service.get_proposal(public_id=proposal_public_id)
    if not proposal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Proposal not found: {proposal_public_id}",
        )

    if not proposal.is_pending:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Proposal is not pending (status: {proposal.status})",
        )

    # Reject the proposal
    updated = agent_service.reject_proposal(
        public_id=proposal_public_id,
        rejected_by=user_email,
        rejection_reason=request.rejection_reason,
    )

    return ProposalActionResponse(
        success=True,
        proposal_public_id=proposal_public_id,
        new_status="rejected",
        message="Proposal rejected. Reason recorded for agent learning.",
    )


@router.post(
    "/vendor/{vendor_public_id}/agent/proposal/{proposal_public_id}/apply",
    response_model=ProposalActionResponse,
    summary="Apply an approved proposal",
    description="Apply an approved proposal to update the vendor.",
)
async def apply_proposal(
    vendor_public_id: str,
    proposal_public_id: str,
    request: ApplyProposalRequest,
    current_user: Annotated[dict, Depends(get_current_user_api)],
):
    """
    Apply an approved proposal to update the vendor.

    This will make the actual changes to the vendor record.
    """
    tenant_id, user_id, user_email = get_user_info(current_user)

    agent_service = VendorAgentService()

    # Get the proposal
    proposal = agent_service.get_proposal(public_id=proposal_public_id)
    if not proposal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Proposal not found: {proposal_public_id}",
        )

    if not proposal.is_approved:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Proposal must be approved before applying (status: {proposal.status})",
        )

    try:
        # Apply the proposal
        updated_proposal, updated_vendor = agent_service.apply_approved_proposal(
            proposal_public_id=proposal_public_id,
            applied_by=user_email,
        )

        return ProposalActionResponse(
            success=True,
            proposal_public_id=proposal_public_id,
            new_status="applied",
            message="Proposal applied successfully. Vendor has been updated.",
        )

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Failed to apply proposal: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to apply proposal",
        )


# =============================================================================
# Batch Processing Endpoint
# =============================================================================

@router.post(
    "/vendor-agent/run-batch",
    response_model=BatchRunResponse,
    summary="Run batch vendor classification",
    description="Run the agent to classify all vendors missing a VendorType.",
)
async def run_batch(
    request: RunBatchClassificationRequest,
    current_user: Annotated[dict, Depends(get_current_user_api)],
):
    """
    Run batch vendor classification.

    The agent will process all vendors missing a VendorType and create
    proposals for classification.
    """
    tenant_id, user_id, user_email = get_user_info(current_user)

    result = run_batch_classification(
        tenant_id=tenant_id,
        user_id=user_email,
        created_by=user_email,
    )

    return BatchRunResponse(
        success=result.get("success", False),
        run_public_id=result.get("run_public_id", ""),
        vendors_processed=result.get("vendors_processed", 0),
        proposals_created=result.get("proposals_created", 0),
        skipped=result.get("skipped", 0),
        error=result.get("error"),
    )


# =============================================================================
# Conversation Management Endpoints
# =============================================================================

@router.delete(
    "/vendor/{vendor_public_id}/agent/conversation",
    summary="Clear conversation history",
    description="Clear the conversation history for a vendor (fresh start).",
)
async def clear_conversation(
    vendor_public_id: str,
    current_user: Annotated[dict, Depends(get_current_user_api)],
):
    """
    Clear the conversation history for a vendor.

    Use this to start fresh if the conversation has become cluttered
    or if you want the agent to re-analyze without prior context.
    """
    # Get vendor
    vendor_service = VendorService()
    vendor = vendor_service.read_by_public_id(public_id=vendor_public_id)
    if not vendor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Vendor not found: {vendor_public_id}",
        )

    agent_service = VendorAgentService()
    agent_service.clear_conversation(vendor_id=vendor.id)

    return {"success": True, "message": "Conversation history cleared"}
