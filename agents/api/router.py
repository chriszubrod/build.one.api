# Python Standard Library Imports
from datetime import datetime, timedelta
from typing import List, Optional

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

# Local Imports
from modules.auth.business.service import get_current_user_api
from agents.admin import WorkflowAdmin
from agents.executor import BillIntakeExecutor
from agents.scheduler import WorkflowScheduler
from agents.notifications.summary import DailySummaryGenerator
from agents.persistence.repo import WorkflowRepository

router = APIRouter(prefix="/api/v1/agents", tags=["api", "agents"])


# ============================================================================
# Request/Response Schemas
# ============================================================================

class ConversationMessageInput(BaseModel):
    """A message within a conversation thread."""
    id: str
    subject: Optional[str] = None
    from_address: Optional[str] = None
    from_name: Optional[str] = None
    to_recipients: List[str] = []
    cc_recipients: List[str] = []
    received_at: Optional[str] = None
    body: Optional[str] = None
    body_type: Optional[str] = None
    has_attachments: bool = False
    attachments: List[dict] = []


class StartWorkflowRequest(BaseModel):
    message_id: str  # The selected/triggered email ID
    conversation_id: Optional[str] = None
    conversation: List[ConversationMessageInput] = []  # Full conversation thread
    total_attachments: int = 0  # Count of all attachments across conversation


class ConfirmTypeRequest(BaseModel):
    entity_type: str  # bill, expense, invoice, contract, change_order, other


class StartWorkflowResponse(BaseModel):
    workflow_id: str
    state: str
    message: str


class WorkflowStatusResponse(BaseModel):
    public_id: str
    state: str
    workflow_type: str
    created_at: Optional[str]
    updated_at: Optional[str]
    vendor: Optional[str]
    invoice_number: Optional[str]
    amount: Optional[float]
    project: Optional[str]
    classification_category: Optional[str]
    classification_confidence: Optional[float]
    qbo_sync_status: Optional[str]
    reminder_count: int
    event_count: int


class WorkflowListItem(BaseModel):
    public_id: str
    state: str
    vendor: Optional[str]
    invoice_number: Optional[str]
    amount: Optional[float]
    created_at: Optional[str]


class StuckWorkflowItem(BaseModel):
    public_id: str
    state: str
    vendor: Optional[str]
    invoice_number: Optional[str]
    last_update: str
    hours_stuck: int


class MetricsResponse(BaseModel):
    period_days: int
    total_created: int
    total_completed: int
    completion_rate: float
    avg_completion_hours: float
    total_value_processed: float
    active_workflows: int
    state_distribution: dict


class RetryRequest(BaseModel):
    from_state: Optional[str] = None


class PollCycleRequest(BaseModel):
    since: Optional[str] = None


class PollCycleResponse(BaseModel):
    new_workflows: int
    replies_processed: int
    orphans_matched: int
    reminders_sent: int
    abandoned: int


class SendSummaryRequest(BaseModel):
    recipients: List[str]
    summary_date: Optional[str] = None


# ============================================================================
# Workflow Status Endpoints
# ============================================================================

@router.get("/workflows", response_model=List[WorkflowListItem])
def list_workflows(
    state: Optional[str] = None,
    current_user: dict = Depends(get_current_user_api),
):
    """
    List all workflows, optionally filtered by state.
    """
    tenant_id = current_user.get("tenant_id", 1)
    repo = WorkflowRepository()
    
    workflows = repo.read_by_tenant_and_state(tenant_id=tenant_id, state=state)
    
    result = []
    for wf in workflows:
        ctx = wf.context or {}
        classification = ctx.get("classification", {})
        vendor_match = ctx.get("vendor_match", {})
        
        result.append(WorkflowListItem(
            public_id=wf.public_id,
            state=wf.state,
            vendor=vendor_match.get("vendor", {}).get("name"),
            invoice_number=classification.get("invoice_number"),
            amount=classification.get("amount"),
            created_at=wf.created_at.isoformat() if wf.created_at else None,
        ))
    
    return result


@router.get("/workflows/{public_id}", response_model=WorkflowStatusResponse)
def get_workflow_status(
    public_id: str,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Get detailed status of a workflow.
    """
    admin = WorkflowAdmin()
    status = admin.get_workflow_status(public_id)
    
    if not status:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )
    
    return WorkflowStatusResponse(**status)


@router.get("/workflows/{public_id}/events")
def get_workflow_events(
    public_id: str,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Get event history for a workflow.
    """
    admin = WorkflowAdmin()
    status = admin.get_workflow_status(public_id)
    
    if not status:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )
    
    return status.get("events", [])


# ============================================================================
# Workflow Control Endpoints
# ============================================================================

async def _unflag_and_mark_read_background(message_id: str) -> None:
    """Remove flag and mark email as read in background. Errors are logged, not raised."""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        from integrations.ms.mail.external.client import unflag_message, mark_message_read
        import asyncio
        
        loop = asyncio.get_event_loop()
        
        # Run both operations in parallel
        unflag_task = loop.run_in_executor(None, unflag_message, message_id)
        read_task = loop.run_in_executor(None, mark_message_read, message_id, True)
        
        unflag_result, read_result = await asyncio.gather(unflag_task, read_task, return_exceptions=True)
        
        # Log unflag result
        if isinstance(unflag_result, Exception):
            logger.warning(f"Failed to unflag message {message_id}: {unflag_result}")
        elif unflag_result.get("status_code") == 200:
            logger.info(f"Removed flag from message {message_id}")
        else:
            logger.warning(f"Failed to unflag message {message_id}: {unflag_result.get('message')}")
        
        # Log mark-as-read result
        if isinstance(read_result, Exception):
            logger.warning(f"Failed to mark message as read {message_id}: {read_result}")
        elif read_result.get("status_code") == 200:
            logger.info(f"Marked message as read {message_id}")
        else:
            logger.warning(f"Failed to mark message as read {message_id}: {read_result.get('message')}")
            
    except Exception as e:
        logger.warning(f"Failed to update message {message_id}: {e}")


@router.post("/workflows/start", response_model=StartWorkflowResponse)
async def start_workflow(
    body: StartWorkflowRequest,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Start an email intake workflow from a conversation.
    
    Accepts the full conversation thread with all messages and attachments
    so the agent can use all available context for classification.
    """
    from agents.persistence.repo import WorkflowRepository
    
    tenant_id = current_user.get("tenant_id", 1)
    access_token = _get_ms_access_token()
    
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No MS Graph access token available. Please connect Microsoft 365 in Integrations.",
        )
    
    # Check for existing workflow with same conversation_id to prevent duplicates
    if body.conversation_id:
        workflow_repo = WorkflowRepository()
        existing = workflow_repo.read_by_conversation_id(body.conversation_id)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A workflow already exists for this conversation (ID: {existing[0].public_id})",
            )
    
    executor = BillIntakeExecutor()
    
    # Convert conversation messages to dicts for storage
    conversation_data = [
        {
            "id": msg.id,
            "subject": msg.subject,
            "from_address": msg.from_address,
            "from_name": msg.from_name,
            "to_recipients": msg.to_recipients,
            "cc_recipients": msg.cc_recipients,
            "received_at": msg.received_at,
            "body": msg.body,
            "body_type": msg.body_type,
            "has_attachments": msg.has_attachments,
            "attachments": msg.attachments,
        }
        for msg in body.conversation
    ]
    
    try:
        workflow = await executor.start_from_email(
            tenant_id=tenant_id,
            access_token=access_token,
            message_id=body.message_id,
            conversation_id=body.conversation_id,
            conversation=conversation_data,
            total_attachments=body.total_attachments,
        )
        
        # Remove flag in background - don't block the response
        import asyncio
        asyncio.create_task(_unflag_and_mark_read_background(body.message_id))
        
        return StartWorkflowResponse(
            workflow_id=workflow.public_id,
            state=workflow.state,
            message="Workflow created - processing in background",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start workflow: {str(e)}",
        )


@router.post("/workflows/{public_id}/retry")
async def retry_workflow(
    public_id: str,
    body: RetryRequest,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Retry a failed workflow - re-runs the triage step.
    """
    admin = WorkflowAdmin()
    repo = WorkflowRepository()
    
    # Get the workflow
    workflow = repo.read_by_public_id(public_id)
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )
    
    # Clear errors and reset state
    success = admin.retry_workflow(public_id, from_state="received")
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to prepare workflow for retry",
        )
    
    # Get access token and re-run triage
    access_token = _get_ms_access_token()
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No MS Graph access token available",
        )
    
    # Re-fetch workflow after state reset
    workflow = repo.read_by_public_id(public_id)
    
    # Run triage
    executor = BillIntakeExecutor()
    try:
        await executor.run_triage(workflow, access_token)
        return {"message": "Workflow retried successfully", "public_id": public_id}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Retry failed: {str(e)}",
        )


@router.post("/workflows/{public_id}/confirm-type")
async def confirm_entity_type(
    public_id: str,
    body: ConfirmTypeRequest,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Confirm the entity type for a classified email workflow.
    
    This transitions the workflow from awaiting_confirmation to confirmed/completed.
    If entity_type is 'bill', spawns a bill_processing workflow in the background.
    """
    from agents.entity_registry import get_entity_config, get_all_entity_types
    import asyncio
    
    repo = WorkflowRepository()
    
    # Validate entity type
    valid_types = get_all_entity_types()
    if body.entity_type not in valid_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid entity type. Must be one of: {', '.join(valid_types)}",
        )
    
    # Get the workflow
    workflow = repo.read_by_public_id(public_id)
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )
    
    # Check workflow is in the right state
    if workflow.state != "awaiting_confirmation":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Workflow is in state '{workflow.state}', not 'awaiting_confirmation'",
        )
    
    # Get entity config for the confirmed type
    entity_config = get_entity_config(body.entity_type)
    
    # Update context with confirmed entity type
    # workflow.context is already a dict (parsed by repository)
    context = workflow.context if isinstance(workflow.context, dict) else {}
    context["confirmed_entity_type"] = body.entity_type
    context["entity_type"] = body.entity_type
    context["entity_label"] = entity_config.label
    context["entity_details_label"] = entity_config.details_label
    context["module"] = entity_config.module
    context["confirmed_by"] = current_user.get("email", "unknown")
    context["confirmed_at"] = datetime.utcnow().isoformat()
    
    # Update workflow state and context
    updated_workflow = repo.update_state(
        public_id=public_id,
        state="completed",  # Email intake is complete, entity workflow can be spawned
        context=context,  # Pass as dict, repo will json.dumps it
    )
    
    # Log event
    from agents.persistence.repo import WorkflowEventRepository
    event_repo = WorkflowEventRepository()
    event_repo.create(
        workflow_id=workflow.id,
        event_type="state_change",
        from_state="awaiting_confirmation",
        to_state="completed",
        created_by=current_user.get("email", "system"),
        data={
            "confirmed_entity_type": body.entity_type,
            "entity_label": entity_config.label,
        },
    )
    
    # Spawn entity-specific workflow based on type
    child_workflow_id = None
    
    if body.entity_type == "bill":
        # Spawn bill_processing workflow in the background
        access_token = _get_ms_access_token()
        
        if access_token:
            executor = BillIntakeExecutor()
            try:
                # Use the updated workflow with context
                # Re-read to get fresh state
                parent_workflow = repo.read_by_public_id(public_id)
                child_workflow = await executor.spawn_bill_processing(
                    parent_workflow=parent_workflow,
                    access_token=access_token,
                )
                child_workflow_id = child_workflow.public_id.lower()
                
                # Update parent with child workflow reference
                context["child_workflow_id"] = child_workflow_id
                context["child_workflow_type"] = "bill_processing"
                repo.update_state(
                    public_id=public_id,
                    state="completed",
                    context=context,
                )
            except Exception as e:
                # Log error but don't fail the confirmation
                import logging
                logger = logging.getLogger(__name__)
                logger.exception(f"Failed to spawn bill_processing workflow: {e}")
    
    response = {
        "message": f"Entity type confirmed as {entity_config.label}",
        "public_id": public_id,
        "entity_type": body.entity_type,
        "entity_label": entity_config.label,
    }
    
    if child_workflow_id:
        response["child_workflow_id"] = child_workflow_id
        response["message"] += f". Bill processing started (workflow: {child_workflow_id})"
    
    return response


@router.post("/workflows/{public_id}/process-bill")
async def trigger_bill_processing(
    public_id: str,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Manually trigger bill processing for a confirmed email_intake workflow.
    
    Use this for workflows that were confirmed as 'bill' before the automatic
    spawn was added, or to re-process a failed bill_processing workflow.
    """
    repo = WorkflowRepository()
    
    # Get the workflow
    workflow = repo.read_by_public_id(public_id)
    if not workflow:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )
    
    # Check workflow is completed and confirmed as bill
    ctx = workflow.context or {}
    confirmed_type = ctx.get("confirmed_entity_type") or ctx.get("entity_type")
    
    if confirmed_type != "bill":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Workflow is not confirmed as 'bill' (type: {confirmed_type})",
        )
    
    # Check if already has a child workflow
    existing_child = ctx.get("child_workflow_id")
    if existing_child:
        # Check if child workflow exists and its state
        child = repo.read_by_public_id(existing_child)
        if child and child.state == "completed":
            return {
                "message": "Bill processing already completed",
                "public_id": public_id,
                "child_workflow_id": existing_child,
                "child_state": child.state,
                "created_bill_public_id": (child.context or {}).get("created_bill_public_id"),
            }
    
    # Get access token
    access_token = _get_ms_access_token()
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MS Graph access token not available",
        )
    
    # Spawn bill processing
    executor = BillIntakeExecutor()
    try:
        child_workflow = await executor.spawn_bill_processing(
            parent_workflow=workflow,
            access_token=access_token,
        )
        
        # Update parent with child workflow reference (lowercase for consistency)
        ctx["child_workflow_id"] = child_workflow.public_id.lower()
        ctx["child_workflow_type"] = "bill_processing"
        repo.update_state(
            public_id=public_id,
            state="completed",
            context=ctx,
        )
        
        return {
            "message": "Bill processing started",
            "public_id": public_id,
            "child_workflow_id": child_workflow.public_id,
        }
        
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception(f"Failed to spawn bill_processing: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start bill processing: {str(e)}",
        )


@router.get("/workflows/pending-bills")
async def get_pending_bill_workflows(
    current_user: dict = Depends(get_current_user_api),
):
    """
    Get all email_intake workflows confirmed as 'bill' that haven't been processed yet.
    
    Returns workflows that:
    - Are in 'completed' state
    - Have confirmed_entity_type = 'bill'
    - Don't have a child_workflow_id OR child workflow is not completed
    """
    tenant_id = current_user.get("tenant_id", 1)
    repo = WorkflowRepository()
    
    # Get completed workflows
    workflows = repo.read_by_tenant_and_state(tenant_id=tenant_id, state="completed")
    
    pending = []
    for wf in workflows:
        ctx = wf.context or {}
        confirmed_type = ctx.get("confirmed_entity_type") or ctx.get("entity_type")
        
        if confirmed_type != "bill":
            continue
        
        # Check if has completed child workflow
        child_id = ctx.get("child_workflow_id")
        if child_id:
            child = repo.read_by_public_id(child_id)
            if child and child.state == "completed":
                continue  # Already processed
        
        pending.append({
            "public_id": wf.public_id,
            "state": wf.state,
            "entity_type": confirmed_type,
            "vendor_name": ctx.get("classification", {}).get("vendor_name") or ctx.get("vendor_match", {}).get("vendor", {}).get("name"),
            "invoice_number": ctx.get("classification", {}).get("invoice_number"),
            "created_at": wf.created_datetime,
            "child_workflow_id": child_id,
            "child_state": None,
        })
        
        # Add child state if exists
        if child_id:
            child = repo.read_by_public_id(child_id)
            if child:
                pending[-1]["child_state"] = child.state
    
    return {
        "count": len(pending),
        "workflows": pending,
    }


# ============================================================================
# Admin & Observability Endpoints
# ============================================================================

@router.get("/admin/stuck", response_model=List[StuckWorkflowItem])
def get_stuck_workflows(
    hours: int = 24,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Get workflows that haven't progressed in the given hours.
    """
    tenant_id = current_user.get("tenant_id", 1)
    admin = WorkflowAdmin()
    
    stuck = admin.get_stuck_workflows(tenant_id, stuck_hours=hours)
    
    return [StuckWorkflowItem(**wf) for wf in stuck]


@router.get("/admin/errors")
def get_error_workflows(
    limit: int = 20,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Get workflows that encountered errors.
    """
    tenant_id = current_user.get("tenant_id", 1)
    admin = WorkflowAdmin()
    
    return admin.get_error_workflows(tenant_id, limit=limit)


@router.get("/admin/metrics", response_model=MetricsResponse)
def get_workflow_metrics(
    days: int = 30,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Get workflow metrics for the given period.
    """
    tenant_id = current_user.get("tenant_id", 1)
    admin = WorkflowAdmin()
    
    metrics = admin.get_workflow_metrics(tenant_id, days=days)
    
    return MetricsResponse(**metrics)


@router.get("/admin/vendors")
def get_vendor_summary(
    days: int = 30,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Get workflow summary grouped by vendor.
    """
    tenant_id = current_user.get("tenant_id", 1)
    admin = WorkflowAdmin()
    
    return admin.get_vendor_summary(tenant_id, days=days)


# ============================================================================
# Email Browse Endpoints (View Only)
# ============================================================================

class EmailPreview(BaseModel):
    id: str
    conversation_id: Optional[str]
    subject: str
    from_address: str
    received_at: Optional[str]
    has_attachments: bool
    is_flagged: bool
    body_preview: str
    web_link: Optional[str] = None


class EmailDetail(BaseModel):
    id: str
    conversation_id: Optional[str]
    subject: str
    from_address: str
    from_name: Optional[str]
    to_recipients: List[str]
    cc_recipients: List[str]
    received_at: Optional[str]
    has_attachments: bool
    is_flagged: bool
    is_read: bool
    importance: str
    body: str
    body_type: str
    web_link: Optional[str]
    attachments: List[dict]


class BrowseInboxResponse(BaseModel):
    emails: List[EmailPreview]
    count: int
    mailbox: Optional[str] = None  # The email address of the mailbox being viewed


class ConversationPreview(BaseModel):
    """A conversation thread summary for inbox browsing (Outlook-style)."""
    conversation_id: str
    subject: str
    participants: List[str]         # All senders in the thread
    latest_message_id: str          # For fetching details
    latest_received_at: Optional[str]
    message_count: int              # Total messages in thread (from fetched data)
    flagged_count: int              # How many are flagged
    has_attachments: bool           # Any message has attachments
    preview: str                    # Most recent message preview
    web_link: Optional[str] = None  # Link to latest message


class BrowseInboxConversationResponse(BaseModel):
    """Response for conversation-grouped inbox browsing."""
    conversations: List[ConversationPreview]
    count: int
    mailbox: Optional[str] = None


def _get_ms_access_token():
    """Get MS Graph access token from database."""
    from integrations.ms.auth.persistence.repo import MsAuthRepository
    
    ms_auth_repo = MsAuthRepository()
    # Get the first/only MS auth record (single-tenant for now)
    auths = ms_auth_repo.read_all()
    if auths and len(auths) > 0:
        return auths[0].access_token
    return None


def _get_current_user_email():
    """Get the email address of the currently authenticated MS user."""
    import requests
    
    access_token = _get_ms_access_token()
    if not access_token:
        return None
    
    try:
        response = requests.get(
            "https://graph.microsoft.com/v1.0/me",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"$select": "mail,userPrincipalName"},
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            # Prefer mail, fall back to userPrincipalName
            return data.get("mail") or data.get("userPrincipalName")
    except Exception:
        pass
    return None


@router.get("/inbox/browse", response_model=BrowseInboxConversationResponse)
async def browse_inbox(
    limit: int = 20,
    has_attachments: Optional[bool] = None,
    flagged: Optional[bool] = True,  # Default: only flagged emails
    is_read: Optional[bool] = True,  # Default: only read emails
    current_user: dict = Depends(get_current_user_api),
):
    """
    Browse recent emails from the inbox, grouped by conversation (Outlook-style).
    
    Returns one entry per conversation thread, sorted by most recent message.
    Automatically excludes conversations that already have workflows.
    
    Filters:
        has_attachments: Filter for emails with/without attachments
        flagged: Filter for flagged (red flag) emails (default: True)
        is_read: Filter for read/unread emails (default: True)
    """
    from agents.capabilities.registry import get_capability_registry
    from agents.persistence.repo import WorkflowRepository
    
    access_token = _get_ms_access_token()
    
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No MS Graph access token available. Please connect Microsoft 365 in Integrations.",
        )
    
    # Get conversation_ids that already have workflows
    workflow_repo = WorkflowRepository()
    existing_conversation_ids = workflow_repo.get_all_conversation_ids()
    
    capabilities = get_capability_registry()
    
    # Fetch more messages than limit since we'll group by conversation
    # and filter out conversations with existing workflows
    # Cap at reasonable amount to avoid slow fetches
    fetch_limit = min(limit * 3, 200)
    
    result = capabilities.email.get_new_messages(
        access_token=access_token,
        folder="inbox",
        has_attachments=has_attachments,
        flagged=flagged,
        is_read=is_read,
        top=fetch_limit,
    )
    
    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch emails: {result.error}",
        )
    
    # Group messages by conversation_id, excluding those with existing workflows
    conversations: dict = {}
    for msg in result.data:
        cid = msg.conversation_id or msg.id  # Fall back to message ID if no conversation
        
        # Skip if this conversation already has a workflow
        if cid in existing_conversation_ids:
            continue
        
        if cid not in conversations:
            conversations[cid] = {
                "messages": [],
                "participants": set(),
                "has_attachments": False,
                "flagged_count": 0,
            }
        
        conv = conversations[cid]
        conv["messages"].append(msg)
        conv["participants"].add(msg.from_address or "Unknown")
        if msg.has_attachments:
            conv["has_attachments"] = True
        if msg.is_flagged:
            conv["flagged_count"] += 1
    
    # Build conversation previews, sorted by most recent message
    conversation_list = []
    for cid, conv in conversations.items():
        # Sort messages by received date (newest first)
        messages = sorted(
            conv["messages"],
            key=lambda m: m.received_datetime or "",
            reverse=True,
        )
        latest = messages[0]
        
        preview = latest.body_preview or (
            latest.body[:200] + "..." if latest.body and len(latest.body) > 200 
            else (latest.body or "")
        )
        
        conversation_list.append(ConversationPreview(
            conversation_id=cid,
            subject=latest.subject or "(No subject)",
            participants=list(conv["participants"]),
            latest_message_id=latest.id,
            latest_received_at=latest.received_datetime,
            message_count=len(messages),
            flagged_count=conv["flagged_count"],
            has_attachments=conv["has_attachments"],
            preview=preview,
            web_link=latest.web_link,
        ))
    
    # Sort by most recent message date
    conversation_list.sort(
        key=lambda c: c.latest_received_at or "",
        reverse=True,
    )
    
    # Limit to requested number of conversations
    conversation_list = conversation_list[:limit]
    
    mailbox_email = _get_current_user_email()
    
    return BrowseInboxConversationResponse(
        conversations=conversation_list,
        count=len(conversation_list),
        mailbox=mailbox_email,
    )


@router.get("/inbox/email/{message_id}", response_model=EmailDetail)
async def get_email_detail(
    message_id: str,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Get full details of a specific email.
    """
    from agents.capabilities.registry import get_capability_registry
    
    access_token = _get_ms_access_token()
    
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No MS Graph access token available. Please connect Microsoft 365 in Integrations.",
        )
    
    capabilities = get_capability_registry()
    
    result = capabilities.email.get_message(
        access_token=access_token,
        message_id=message_id,
    )
    
    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch email: {result.error}",
        )
    
    msg = result.data
    return EmailDetail(
        id=msg.id,
        conversation_id=msg.conversation_id,
        subject=msg.subject or "(No subject)",
        from_address=msg.from_address or "Unknown",
        from_name=msg.from_name,
        to_recipients=msg.to_recipients,
        cc_recipients=msg.cc_recipients,
        received_at=msg.received_datetime,
        has_attachments=msg.has_attachments,
        is_flagged=msg.is_flagged,
        is_read=msg.is_read,
        importance=msg.importance,
        body=msg.body or "",
        body_type=msg.body_type,
        web_link=msg.web_link,
        attachments=msg.attachments,
    )


@router.post("/inbox/email/{message_id}/unflag")
async def unflag_email(
    message_id: str,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Remove the flag from an email.
    """
    from integrations.ms.mail.external.client import unflag_message
    
    result = unflag_message(message_id)
    
    if result.get("status_code") != 200:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message", "Failed to unflag email"),
        )
    
    return {"message": "Flag removed successfully"}


class ConversationMessage(BaseModel):
    """A message within a conversation thread."""
    id: str
    subject: str
    from_address: str
    from_name: Optional[str]
    to_recipients: List[str]
    cc_recipients: List[str]
    received_at: Optional[str]
    has_attachments: bool
    is_read: bool
    importance: str
    body: str
    body_type: str
    attachments: List[dict]
    web_link: Optional[str]


class ConversationThreadResponse(BaseModel):
    """Response containing all messages in a conversation."""
    conversation_id: str
    messages: List[ConversationMessage]
    count: int


@router.get("/inbox/thread/{conversation_id}")
async def get_conversation_thread(
    conversation_id: str,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Get all messages in a conversation thread.
    
    Returns messages sorted by date (oldest first) to show conversation flow.
    """
    from agents.capabilities.registry import get_capability_registry
    
    access_token = _get_ms_access_token()
    
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No MS Graph access token available. Please connect Microsoft 365 in Integrations.",
        )
    
    capabilities = get_capability_registry()
    
    result = capabilities.email.get_thread_messages(
        access_token=access_token,
        conversation_id=conversation_id,
    )
    
    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch conversation: {result.error}",
        )
    
    messages = result.data or []
    
    # Sort by received date (oldest first for conversation view)
    messages.sort(key=lambda m: m.received_datetime or "")
    
    return ConversationThreadResponse(
        conversation_id=conversation_id,
        messages=[
            ConversationMessage(
                id=msg.id,
                subject=msg.subject or "(No subject)",
                from_address=msg.from_address or "Unknown",
                from_name=msg.from_name,
                to_recipients=msg.to_recipients or [],
                cc_recipients=msg.cc_recipients or [],
                received_at=msg.received_datetime,
                has_attachments=msg.has_attachments,
                is_read=msg.is_read,
                importance=msg.importance or "normal",
                body=msg.body or "",
                body_type=msg.body_type,
                attachments=msg.attachments,
                web_link=msg.web_link,
            )
            for msg in messages
        ],
        count=len(messages),
    )


@router.get("/inbox/email/{message_id}/attachment/{attachment_index}")
async def get_attachment(
    message_id: str,
    attachment_index: int,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Download a specific attachment from an email.
    
    Returns the raw attachment content with appropriate content-type.
    """
    from fastapi.responses import Response
    from agents.capabilities.registry import get_capability_registry
    
    access_token = _get_ms_access_token()
    
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No MS Graph access token available. Please connect Microsoft 365 in Integrations.",
        )
    
    capabilities = get_capability_registry()
    
    # First get the email to get attachment info
    result = capabilities.email.get_message(
        access_token=access_token,
        message_id=message_id,
    )
    
    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch email: {result.error}",
        )
    
    msg = result.data
    attachments = msg.attachments or []
    
    if attachment_index < 0 or attachment_index >= len(attachments):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Attachment not found",
        )
    
    attachment = attachments[attachment_index]
    attachment_id = attachment.get("id") or attachment.get("Id")
    attachment_name = attachment.get("name") or attachment.get("Name") or "attachment"
    content_type = attachment.get("contentType") or attachment.get("ContentType") or "application/octet-stream"
    
    # Download the attachment content
    download_result = capabilities.email.download_attachment(
        access_token=access_token,
        message_id=message_id,
        attachment_id=attachment_id,
    )
    
    if not download_result.success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to download attachment: {download_result.error}",
        )
    
    # Extract content from the result dict
    data = download_result.data
    if isinstance(data, dict):
        content = data.get("content")
        # Use content_type from download if available
        if data.get("content_type"):
            content_type = data.get("content_type")
    else:
        content = data
    
    if content is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Attachment content is empty",
        )
    
    # Ensure content is bytes
    if isinstance(content, str):
        content = content.encode('utf-8')
    elif not isinstance(content, bytes):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected content type: {type(content).__name__}",
        )
    
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": f'inline; filename="{attachment_name}"',
        }
    )


# ============================================================================
# Polling & Notifications Endpoints
# ============================================================================

@router.post("/poll/run", response_model=PollCycleResponse)
async def run_poll_cycle(
    body: PollCycleRequest,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Run a full polling cycle: fetch new emails, check replies, process timeouts.
    """
    tenant_id = current_user.get("tenant_id", 1)
    access_token = _get_ms_access_token()
    
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No MS Graph access token available. Please connect Microsoft 365 in Integrations.",
        )
    
    scheduler = WorkflowScheduler()
    
    results = await scheduler.run_full_cycle(
        tenant_id=tenant_id,
        access_token=access_token,
        since=body.since,
    )
    
    return PollCycleResponse(**results)


@router.post("/notifications/daily-summary")
def send_daily_summary(
    body: SendSummaryRequest,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Generate and send the daily workflow summary email.
    """
    tenant_id = current_user.get("tenant_id", 1)
    access_token = _get_ms_access_token()
    
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No MS Graph access token available. Please connect Microsoft 365 in Integrations.",
        )
    
    summary_date = None
    if body.summary_date:
        try:
            summary_date = datetime.fromisoformat(body.summary_date)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date format. Use ISO format (YYYY-MM-DD).",
            )
    
    generator = DailySummaryGenerator()
    success = generator.send_summary(
        tenant_id=tenant_id,
        access_token=access_token,
        recipients=body.recipients,
        summary_date=summary_date,
    )
    
    if success:
        return {"message": "Daily summary sent", "recipients": body.recipients}
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send daily summary",
        )
