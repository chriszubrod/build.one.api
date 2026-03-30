# Python Standard Library Imports
import logging
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Local Imports
from shared.rbac import require_module_web
from shared.rbac_constants import Modules


logger = logging.getLogger(__name__)

router = APIRouter(tags=["web", "dashboard"])
templates = Jinja2Templates(directory="templates")


@router.get("/dashboard")
async def dashboard(request: Request, current_user: dict = Depends(require_module_web(Modules.DASHBOARD))):
    """
    User dashboard - personalized landing page.
    Shows tasks, projects, and activity relevant to the logged-in user.
    """
    return templates.TemplateResponse(
        "dashboard/view.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
