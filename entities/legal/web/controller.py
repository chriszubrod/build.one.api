# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Local Imports
from entities.auth.business.service import get_current_user_web

router = APIRouter(prefix="/legal", tags=["web", "legal"])
templates = Jinja2Templates(directory="templates")


@router.get("/eula")
async def eula(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    End User License Agreement page.
    """
    return templates.TemplateResponse(
        "legal/eula.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/privacy")
async def privacy_policy(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Privacy Policy page.
    """
    return templates.TemplateResponse(
        "legal/privacy.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )

