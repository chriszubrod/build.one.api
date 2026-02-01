# Python Standard Library Imports
import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

# Local Imports
from services.auth.business.service import get_current_user_api
from services.admin.api.schemas import ApproveRequest, CancelRequest, RejectRequest
from services.tasks.api.schemas import StartWorkflowRequest, PollRunResponse
from services.tasks.business.service import TaskService
from workflows.admin import WorkflowAdmin
from workflows.executor import BillIntakeExecutor
from workflows.persistence.repo import WorkflowRepository
from workflows.scheduler import WorkflowScheduler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tasks", tags=["api", "tasks"])


# -----------------------------------------------------------------------------
# List and Detail
# -----------------------------------------------------------------------------

@router.get("")
def list_tasks(
    status: Optional[str] = Query(default=None, description="Filter by task status"),
    source_type: Optional[str] = Query(default=None, description="Filter by source type (e.g. email)"),
    source_id: Optional[str] = Query(default=None, description="Filter by source id (e.g. conversation_id)"),
    open_only: bool = Query(default=True, description="Exclude completed/cancelled"),
    current_user: dict = Depends(get_current_user_api),
) -> List[Dict]:
    """List tasks for the tenant with optional filters."""
    tenant_id = current_user.get("tenant_id", 1)
    svc = TaskService()
    return svc.get_tasks_for_list(
        tenant_id=tenant_id,
        status=status,
        source_type=source_type,
        source_id=source_id,
        open_only=open_only,
    )


@router.get("/{public_id}")
def get_task_detail(
    public_id: str,
    current_user: dict = Depends(get_current_user_api),
) -> Dict:
    """Get task by public_id and resolved entity (e.g. workflow with events)."""
    svc = TaskService()
    result = svc.get_task_detail(public_id)
    if not result or not result.get("task"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Task not found: {public_id}")
    return result


# -----------------------------------------------------------------------------
# Workflow Start and Poll
# -----------------------------------------------------------------------------

@router.post("/workflows/start")
async def start_workflow(
    body: StartWorkflowRequest,
    current_user: dict = Depends(get_current_user_api),
) -> Dict:
    """Start a workflow from email (e.g. from tasks browse). Creates/updates Task row."""
    tenant_id = current_user.get("tenant_id", 1)
    access_token = _get_ms_access_token(tenant_id)
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MS Graph token not available. Connect Microsoft integration first.",
        )
    executor = BillIntakeExecutor()
    workflow = await executor.start_from_email(
        tenant_id=tenant_id,
        access_token=access_token,
        message_id=body.message_id,
        conversation_id=body.conversation_id,
        conversation=body.conversation,
        total_attachments=body.total_attachments,
    )
    return {
        "workflow_id": workflow.public_id,
        "workflow_type": workflow.workflow_type,
        "state": workflow.state,
    }


@router.post("/poll/run")
async def poll_run(
    current_user: dict = Depends(get_current_user_api),
) -> PollRunResponse:
    """Run inbox poll (create workflows from new emails). New workflows get Task rows via executor hook."""
    tenant_id = current_user.get("tenant_id", 1)
    access_token = _get_ms_access_token(tenant_id)
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MS Graph token not available. Connect Microsoft integration first.",
        )
    scheduler = WorkflowScheduler()
    created = await scheduler.poll_inbox(
        tenant_id=tenant_id,
        access_token=access_token,
    )
    return PollRunResponse(
        new_workflows=len(created),
        replies_processed=0,
        reminders_sent=0,
    )


def _get_ms_access_token(tenant_id: int) -> Optional[str]:
    """Get MS Graph access token for the tenant."""
    try:
        from integrations.ms.auth.business.service import MsAuthService
        ms_auth = MsAuthService().ensure_valid_token(tenant_id=tenant_id)
        return ms_auth.access_token if ms_auth else None
    except Exception as e:
        logger.warning("Failed to get MS token for tenant %s: %s", tenant_id, e)
        return None


# -----------------------------------------------------------------------------
# Inbox Browse and Thread
# -----------------------------------------------------------------------------

@router.get("/inbox/browse")
def inbox_browse(
    limit: int = Query(default=25, ge=1, le=100),
    current_user: dict = Depends(get_current_user_api),
) -> Dict:
    """Fetch conversations (grouped messages) for tasks browse page."""
    tenant_id = current_user.get("tenant_id", 1)
    access_token = _get_ms_access_token(tenant_id)
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MS Graph token not available. Connect Microsoft integration first.",
        )
    try:
        from integrations.ms.mail.external.client import list_messages
        resp = list_messages(folder="inbox", top=min(limit * 3, 150))
    except Exception as e:
        logger.exception("inbox list_messages failed")
        raise HTTPException(status_code=500, detail=str(e))
    if resp.get("status_code") != 200:
        raise HTTPException(
            status_code=resp.get("status_code", 500),
            detail=resp.get("message", "Failed to list messages"),
        )
    messages = resp.get("messages", [])
    # Group by conversation_id
    by_conv: Dict[str, List[Dict]] = defaultdict(list)
    for m in messages:
        cid = m.get("conversation_id") or m.get("conversationId") or ""
        if cid:
            by_conv[cid].append(m)
    conversations = []
    for cid, msgs in list(by_conv.items())[:limit]:
        msgs_sorted = sorted(msgs, key=lambda x: x.get("received_datetime") or x.get("receivedDateTime") or "", reverse=True)
        latest = msgs_sorted[0]
        participants = list({(m.get("from_name") or m.get("from_email") or "Unknown") for m in msgs_sorted})
        flagged_count = sum(1 for m in msgs_sorted if (m.get("flag") or {}).get("flagStatus") == "flagged")
        has_attachments = any(m.get("has_attachments") or m.get("hasAttachments") for m in msgs_sorted)
        conversations.append({
            "conversation_id": cid,
            "subject": latest.get("subject") or "(No subject)",
            "participants": participants,
            "preview": (latest.get("body_preview") or latest.get("bodyPreview") or "")[:150],
            "latest_received_at": latest.get("received_datetime") or latest.get("receivedDateTime"),
            "message_count": len(msgs_sorted),
            "has_attachments": has_attachments,
            "flagged_count": flagged_count,
            "latest_message_id": latest.get("message_id") or latest.get("id"),
            "web_link": latest.get("web_link") or latest.get("webLink"),
        })
    mailbox = getattr(current_user, "email", None) or "Inbox"
    return {"conversations": conversations, "count": len(conversations), "mailbox": mailbox}


@router.get("/inbox/thread/{conversation_id}")
def inbox_thread(
    conversation_id: str,
    current_user: dict = Depends(get_current_user_api),
) -> Dict:
    """Fetch a single conversation thread by conversation_id."""
    tenant_id = current_user.get("tenant_id", 1)
    _ = _get_ms_access_token(tenant_id)
    try:
        from integrations.ms.mail.external.client import search_all_messages
        resp = search_all_messages(conversation_id=conversation_id, top=50)
    except Exception as e:
        logger.exception("inbox thread failed")
        raise HTTPException(status_code=500, detail=str(e))
    if resp.get("status_code") != 200:
        raise HTTPException(
            status_code=resp.get("status_code", 500),
            detail=resp.get("message", "Failed to load thread"),
        )
    messages = resp.get("messages", [])
    # Normalize for template: id, subject, from_address, from_name, to_recipients, received_at, body, body_type, has_attachments, attachments
    out = []
    for m in messages:
        from_addr = (m.get("from") or {})
        if isinstance(from_addr, dict):
            from_email = from_addr.get("email") or from_addr.get("from_email")
            from_name = from_addr.get("name") or from_addr.get("from_name")
        else:
            from_email = m.get("from_email")
            from_name = m.get("from_name")
        to_list = m.get("to_recipients") or []
        if to_list and isinstance(to_list[0], dict):
            to_recipients = [r.get("email") or r.get("address") for r in to_list]
        else:
            to_recipients = to_list
        out.append({
            "id": m.get("message_id") or m.get("id"),
            "subject": m.get("subject"),
            "from_address": from_email,
            "from_name": from_name,
            "to_recipients": to_recipients or [],
            "cc_recipients": m.get("cc_recipients") or [],
            "received_at": m.get("received_datetime") or m.get("receivedDateTime"),
            "body": m.get("body_content") or m.get("body", ""),
            "body_type": (m.get("body_content_type") or m.get("contentType") or "text").lower(),
            "has_attachments": m.get("has_attachments") or m.get("hasAttachments", False),
            "attachments": m.get("attachments") or [],
            "web_link": m.get("web_link") or m.get("webLink"),
        })
    return {"conversation_id": conversation_id, "messages": out, "count": len(out)}


# -----------------------------------------------------------------------------
# Workflow Action Proxies (task detail UI calls these)
# -----------------------------------------------------------------------------

@router.post("/workflows/{public_id}/retry")
def workflow_retry(
    public_id: str,
    current_user: dict = Depends(get_current_user_api),
):
    """Retry a failed workflow."""
    user_id = current_user.get("id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User ID not found")
    admin = WorkflowAdmin()
    try:
        return admin.retry_workflow(public_id, user_id=user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/workflows/{public_id}/cancel")
def workflow_cancel(
    public_id: str,
    body: CancelRequest,
    current_user: dict = Depends(get_current_user_api),
):
    """Cancel a workflow."""
    user_id = current_user.get("id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User ID not found")
    admin = WorkflowAdmin()
    try:
        return admin.cancel_workflow(public_id, user_id=user_id, reason=body.reason)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/workflows/{public_id}/approve")
def workflow_approve(
    public_id: str,
    body: ApproveRequest,
    current_user: dict = Depends(get_current_user_api),
):
    """Approve a workflow."""
    user_id = current_user.get("id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User ID not found")
    admin = WorkflowAdmin()
    try:
        return admin.approve_workflow(public_id, user_id=user_id, project_id=body.project_id, cost_code=body.cost_code)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/workflows/{public_id}/reject")
def workflow_reject(
    public_id: str,
    body: RejectRequest,
    current_user: dict = Depends(get_current_user_api),
):
    """Reject a workflow."""
    user_id = current_user.get("id")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User ID not found")
    admin = WorkflowAdmin()
    try:
        return admin.reject_workflow(public_id, user_id=user_id, reason=body.reason)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/workflows/{public_id}/reminder")
async def workflow_send_reminder(
    public_id: str,
    current_user: dict = Depends(get_current_user_api),
):
    """Send a reminder email for a workflow awaiting approval."""
    tenant_id = current_user.get("tenant_id", 1)
    access_token = _get_ms_access_token(tenant_id)
    if not access_token:
        raise HTTPException(status_code=400, detail="MS Graph token not available.")
    wf_repo = WorkflowRepository()
    workflow = wf_repo.read_by_public_id(public_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    executor = BillIntakeExecutor()
    await executor.send_reminder(workflow, access_token=access_token)
    return {"success": True, "message": "Reminder sent"}


class ConfirmTypeRequest(BaseModel):
    entity_type: str


@router.post("/workflows/{public_id}/confirm-type")
def workflow_confirm_type(
    public_id: str,
    body: ConfirmTypeRequest,
    current_user: dict = Depends(get_current_user_api),
):
    """Confirm entity type for a workflow in awaiting_confirmation state."""
    from workflows.orchestrator import WorkflowOrchestrator
    orchestrator = WorkflowOrchestrator()
    try:
        updated = orchestrator.transition(
            public_id=public_id,
            trigger="confirm_type",
            context_updates={"confirmed_entity_type": body.entity_type},
            created_by=f"user:{current_user.get('id', 0)}",
        )
        return {"success": True, "state": updated.state}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/workflows/{public_id}/process-bill")
async def workflow_process_bill(
    public_id: str,
    current_user: dict = Depends(get_current_user_api),
):
    """Spawn bill_processing workflow from a confirmed email_intake workflow."""
    tenant_id = current_user.get("tenant_id", 1)
    access_token = _get_ms_access_token(tenant_id)
    if not access_token:
        raise HTTPException(status_code=400, detail="MS Graph token not available.")
    wf_repo = WorkflowRepository()
    workflow = wf_repo.read_by_public_id(public_id)
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    executor = BillIntakeExecutor()
    child = await executor.spawn_bill_processing(workflow, access_token=access_token)
    try:
        TaskService().upsert_task_for_workflow(child)
    except Exception as e:
        logger.warning("Failed to upsert task for bill_processing workflow: %s", e)
    return {"success": True, "workflow_id": child.public_id, "state": child.state}


@router.get("/inbox/email/{message_id}/attachment/{attachment_index:int}")
def inbox_attachment(
    message_id: str,
    attachment_index: int,
    current_user: dict = Depends(get_current_user_api),
):
    """Download an email attachment by message ID and attachment index."""
    tenant_id = current_user.get("tenant_id", 1)
    _ = _get_ms_access_token(tenant_id)
    try:
        from fastapi.responses import Response
        from integrations.ms.mail.external.client import list_message_attachments, download_attachment
        attachments_resp = list_message_attachments(message_id)
        if attachments_resp.get("status_code") != 200:
            raise HTTPException(status_code=attachments_resp.get("status_code", 500), detail=attachments_resp.get("message"))
        attachments = attachments_resp.get("attachments", [])
        if attachment_index < 0 or attachment_index >= len(attachments):
            raise HTTPException(status_code=404, detail="Attachment not found")
        att = attachments[attachment_index]
        att_id = att.get("attachment_id") or att.get("id")
        result = download_attachment(message_id, att_id)
        if result.get("status_code") != 200:
            raise HTTPException(status_code=result.get("status_code", 500), detail=result.get("message"))
        content = result.get("content")
        content_type = result.get("content_type") or "application/octet-stream"
        filename = (att.get("name") or result.get("filename") or "attachment").replace('"', "'")
        return Response(content=content or b"", media_type=content_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("inbox_attachment failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/inbox/email/{message_id}/unflag")
def inbox_unflag(
    message_id: str,
    current_user: dict = Depends(get_current_user_api),
):
    """Remove flag from an email message."""
    tenant_id = current_user.get("tenant_id", 1)
    _ = _get_ms_access_token(tenant_id)
    try:
        from integrations.ms.mail.external.client import unflag_message
        result = unflag_message(message_id)
        if result.get("status_code") not in (200, 204):
            raise HTTPException(status_code=result.get("status_code", 500), detail=result.get("message"))
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("inbox_unflag failed")
        raise HTTPException(status_code=500, detail=str(e))
