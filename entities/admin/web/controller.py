# Python Standard Library Imports
import logging
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from shared.rbac import require_module_web
from shared.rbac_constants import Modules
from core.workflow.business.admin import WorkflowAdmin


# Initialize the logger
logger = logging.getLogger(__name__)

router = APIRouter(tags=["web", "admin"])
templates = Jinja2Templates(directory="templates")


@router.get("/admin")
async def admin(request: Request, current_user: dict = Depends(require_module_web(Modules.ROLES))):
    """
    Admin dashboard - workflow monitoring.
    """
    # Initialize the variables for dashboard data
    flash_message = None
    flash_type = None

    return templates.TemplateResponse(
        "admin/view.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
            "flash_message": flash_message,
            "flash_type": flash_type,
        },
    )


@router.get("/admin/workflow/{public_id}")
async def workflow_detail(
    public_id: str,
    request: Request,
    current_user: dict = Depends(require_module_web(Modules.ROLES)),
):
    """
    Workflow detail view showing audit trail.
    """
    admin = WorkflowAdmin()
    workflow_data = admin.get_workflow_with_events(public_id)
    
    if not workflow_data:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    return templates.TemplateResponse(
        "admin/workflow_detail.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
            "workflow": workflow_data.get("workflow"),
            "events": workflow_data.get("events", []),
            "summary": workflow_data.get("summary"),
        },
    )
