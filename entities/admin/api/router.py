# Python Standard Library Imports
from datetime import datetime
from typing import List, Optional

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, Query, status

# Local Imports
from entities.auth.business.service import get_current_user_api
from entities.admin.api.schemas import ApproveRequest, CancelRequest, RejectRequest
from workflows.admin import WorkflowAdmin

router = APIRouter(prefix="/api/v1", tags=["api", "admin"])


@router.get("/admin/stats")
def get_dashboard_stats(current_user: dict = Depends(get_current_user_api)):
    """
    Get workflow statistics for dashboard.
    
    Returns summary stats including workflows today, completed, awaiting approval,
    failed in last 24h, and active workflows.
    """
    tenant_id = current_user.get("tenant_id", 1)
    admin = WorkflowAdmin()
    return admin.get_dashboard_stats(tenant_id)


@router.get("/admin/recent-workflows")
def get_recent_workflows(
    limit: int = Query(default=50, ge=1, le=100),
    current_user: dict = Depends(get_current_user_api),
):
    """
    Get recent workflows for dashboard display.
    
    Returns a list of recent workflows with key details for the table view.
    """
    tenant_id = current_user.get("tenant_id", 1)
    admin = WorkflowAdmin()
    return admin.get_recent_workflows(tenant_id, limit=limit)


@router.get("/admin/workflows-by-state")
def get_workflows_by_state(
    state: str = Query(..., description="Workflow state to filter by"),
    current_user: dict = Depends(get_current_user_api),
):
    """
    Get workflows in a specific state.
    """
    tenant_id = current_user.get("tenant_id", 1)
    admin = WorkflowAdmin()
    return admin.get_workflows_by_state(tenant_id, state=state)


@router.get("/admin/failed-workflows")
def get_failed_workflows(
    limit: int = Query(default=20, ge=1, le=50),
    current_user: dict = Depends(get_current_user_api),
):
    """
    Get recent failed workflows for debugging.
    
    Returns failed workflows with error details.
    """
    tenant_id = current_user.get("tenant_id", 1)
    admin = WorkflowAdmin()
    return admin.get_failed_workflows(tenant_id, limit=limit)


@router.get("/workflows/search")
def search_workflows(
    q: Optional[str] = Query(default=None, description="Search term - matches vendor, invoice #, or amount"),
    state: Optional[str] = Query(default=None, description="Filter by workflow state"),
    limit: int = Query(default=50, ge=1, le=100),
    current_user: dict = Depends(get_current_user_api),
) -> List[dict]:
    """
    Search workflows by vendor name, invoice number, or amount.
    
    Provides unified search across key workflow fields with optional state filtering.
    """
    tenant_id = current_user.get("tenant_id", 1)
    admin = WorkflowAdmin()
    return admin.search_workflows(tenant_id, q=q, state=state, limit=limit)


@router.get("/workflow/{public_id}")
def get_workflow_detail(
    public_id: str,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Get workflow with full event history.
    
    Returns the workflow details along with all events for audit trail display.
    """
    admin = WorkflowAdmin()
    result = admin.get_workflow_with_events(public_id)
    
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Workflow not found: {public_id}"
        )
    
    return result


# -----------------------------------------------------------------------------
# Notification Endpoints
# -----------------------------------------------------------------------------

@router.get("/notifications/poll")
def poll_notifications(
    since: str = Query(..., description="ISO timestamp to fetch notifications after"),
    current_user: dict = Depends(get_current_user_api),
) -> List[dict]:
    """
    Poll for new workflow notifications.
    
    Returns workflow state transition events (to failed, awaiting_approval, or completed)
    that occurred after the given timestamp. Used for real-time notification updates.
    
    Args:
        since: ISO format timestamp (e.g., 2024-01-28T10:00:00Z)
        
    Returns:
        List of notification objects with workflow details and summary
    """
    tenant_id = current_user.get("tenant_id", 1)
    
    # Parse the since timestamp
    try:
        # Handle various ISO formats
        since_dt = datetime.fromisoformat(since.replace("Z", "+00:00").split("+")[0])
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid timestamp format. Use ISO format (e.g., 2024-01-28T10:00:00Z)"
        )
    
    admin = WorkflowAdmin()
    return admin.get_recent_notifications(tenant_id, since=since_dt)


# -----------------------------------------------------------------------------
# Workflow Action Endpoints
# -----------------------------------------------------------------------------

@router.post("/workflow/{public_id}/retry")
def retry_workflow(
    public_id: str,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Retry a failed workflow.
    
    Only works for workflows in 'failed' or 'needs_review' state.
    Triggers a retry from the last failed step.
    """
    user_id = current_user.get("id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User ID not found in token"
        )
    
    admin = WorkflowAdmin()
    try:
        return admin.retry_workflow(public_id, user_id=user_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/workflow/{public_id}/cancel")
def cancel_workflow(
    public_id: str,
    body: CancelRequest,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Cancel a workflow.
    
    Works for any workflow not in a final state.
    """
    user_id = current_user.get("id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User ID not found in token"
        )
    
    admin = WorkflowAdmin()
    try:
        return admin.cancel_workflow(
            public_id,
            user_id=user_id,
            reason=body.reason,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/workflow/{public_id}/approve")
def approve_workflow(
    public_id: str,
    body: ApproveRequest,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Manually approve a workflow.
    
    Alternative to email-based approval flow. Only works for workflows
    in 'awaiting_approval' state.
    """
    user_id = current_user.get("id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User ID not found in token"
        )
    
    admin = WorkflowAdmin()
    try:
        return admin.approve_workflow(
            public_id,
            user_id=user_id,
            project_id=body.project_id,
            cost_code=body.cost_code,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/workflow/{public_id}/reject")
def reject_workflow(
    public_id: str,
    body: RejectRequest,
    current_user: dict = Depends(get_current_user_api),
):
    """
    Reject a workflow awaiting approval.
    
    Only works for workflows in 'awaiting_approval' state.
    """
    user_id = current_user.get("id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User ID not found in token"
        )
    
    admin = WorkflowAdmin()
    try:
        return admin.reject_workflow(
            public_id,
            user_id=user_id,
            reason=body.reason,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
