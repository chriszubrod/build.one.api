# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Local Imports
from shared.rbac import require_module_web
from shared.rbac_constants import Modules

logger = logging.getLogger(__name__)

router = APIRouter(tags=["web", "admin"])
templates = Jinja2Templates(directory="templates")


@router.get("/admin/overrides")
async def admin_overrides(
    request: Request,
    current_user: dict = Depends(require_module_web(Modules.CLASSIFICATION_OVERRIDES)),
):
    """Admin page for managing classification overrides."""
    return templates.TemplateResponse(
        "admin/overrides.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
