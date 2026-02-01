# Python Standard Library Imports
import logging
from collections import Counter
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Request, Depends, HTTPException, Query

# Local Imports
from services.auth.business.service import get_current_user_web
from services.tasks.business.service import TaskService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["web", "tasks"])
templates = __import__("fastapi.templating", fromlist=["Jinja2Templates"]).Jinja2Templates(directory="templates")


def _templates():
    from fastapi.templating import Jinja2Templates
    return Jinja2Templates(directory="templates")


@router.get("")
@router.get("/list")
async def list_tasks(
    request: Request,
    current_user: dict = Depends(get_current_user_web),
    state: Optional[str] = Query(default=None, description="Filter by status/state"),
    open_only: bool = Query(default=True, description="Only open tasks"),
):
    """Tasks list page."""
    tenant_id = current_user.get("tenant_id", 1)
    svc = TaskService()
    tasks = svc.get_tasks_for_list(
        tenant_id=tenant_id,
        status=state,
        open_only=open_only,
    )
    state_counts = Counter(t.get("state") or t.get("status") or "open" for t in tasks)
    metrics = {"active_workflows": len([t for t in tasks if (t.get("state") or t.get("status")) not in ("completed", "cancelled")])}
    templates = _templates()
    return templates.TemplateResponse(
        "task/list.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
            "tasks": tasks,
            "workflows": tasks,
            "metrics": metrics,
            "state_counts": dict(state_counts),
            "selected_state": state,
        },
    )


@router.get("/browse")
async def browse(
    request: Request,
    current_user: dict = Depends(get_current_user_web),
):
    """Email/conversations browse page."""
    templates = _templates()
    return templates.TemplateResponse(
        "task/browse.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/not-found")
async def not_found(
    request: Request,
    current_user: dict = Depends(get_current_user_web),
    public_id: Optional[str] = Query(default=None),
):
    """Task or workflow not found page."""
    templates = _templates()
    return templates.TemplateResponse(
        "task/not_found.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
            "public_id": public_id or "",
        },
    )


@router.get("/{public_id}")
async def task_detail(
    public_id: str,
    request: Request,
    current_user: dict = Depends(get_current_user_web),
    from_state: Optional[str] = Query(default=None),
):
    """Task detail by public_id; resolves workflow and renders task view."""
    svc = TaskService()
    result = svc.get_task_detail(public_id)
    if not result or not result.get("task"):
        templates = _templates()
        return templates.TemplateResponse(
            "task/not_found.html",
            {
                "request": request,
                "current_user": current_user,
                "current_path": request.url.path,
                "public_id": public_id,
            },
            status_code=404,
        )
    workflow_data = result.get("workflow")
    events_raw = result.get("events", [])
    summary = result.get("summary")
    if workflow_data:
        wf_dict = workflow_data if isinstance(workflow_data, dict) else getattr(workflow_data, "to_dict", lambda: {})()
    else:
        wf_dict = {}
    if summary:
        wf_dict.update(summary)
    # Ensure conversation and counts are at top level for template (from context)
    ctx = wf_dict.get("context") or {}
    wf_dict.setdefault("conversation", ctx.get("conversation") or [])
    conv = wf_dict.get("conversation") or []
    wf_dict.setdefault("message_count", len(conv))
    wf_dict.setdefault("attachment_count", ctx.get("total_attachments", 0))
    # Build events list; events_raw may be dicts (from to_dict()) or objects
    events = []
    for e in events_raw:
        if hasattr(e, "get"):
            events.append({
                "from_state": e.get("from_state"),
                "to_state": e.get("to_state"),
                "step_name": e.get("step_name"),
                "type": e.get("event_type"),
                "created_at": e.get("created_datetime"),
                "created_by": e.get("created_by"),
            })
        else:
            events.append({
                "from_state": getattr(e, "from_state", None),
                "to_state": getattr(e, "to_state", None),
                "step_name": getattr(e, "step_name", None),
                "type": getattr(e, "event_type", None),
                "created_at": getattr(e, "created_datetime", None),
                "created_by": getattr(e, "created_by", None),
            })
    wf_dict["events"] = events

    # Extract upload data from task context for data_upload tasks
    task_dict = result["task"]
    upload_data = None
    if task_dict.get("task_type") == "data_upload" and task_dict.get("context"):
        upload_data = task_dict["context"] if isinstance(task_dict["context"], dict) else {}

    templates = _templates()
    return templates.TemplateResponse(
        "task/view.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
            "task": task_dict,
            "workflow": wf_dict,
            "upload": upload_data,
            "events": events,
            "return_url": f"/tasks/list{f'?state={from_state}' if from_state else ''}",
        },
    )
