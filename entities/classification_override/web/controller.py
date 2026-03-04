# Python Standard Library Imports
import logging

# Third-party Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Local Imports
from entities.auth.business.service import get_current_user_web

logger = logging.getLogger(__name__)

router = APIRouter(tags=["web", "admin"])
templates = Jinja2Templates(directory="templates")


@router.get("/admin/overrides")
async def admin_overrides(
    request: Request,
    current_user: dict = Depends(get_current_user_web),
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
