# Python Standard Library Imports
import logging
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse

# Third-party Imports

# Local Imports
from modules.auth.business.service import get_current_user_web


# Initialize the logger
logger = logging.getLogger(__name__)

router = APIRouter(tags=["web", "dashboard"])
templates = Jinja2Templates(directory="templates")


@router.get("/dashboard")
async def dashboard(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Dashboard.
    """
    print(f"Current user: {current_user}")
    print(f"Request: {request}")

    # Check if current_user is a RedirectResponse (authentication failed)
    if isinstance(current_user, RedirectResponse):
        return current_user

    # Initialize the variables for dashboard data
    flash_message = None
    flash_type = None

    return templates.TemplateResponse(
        "dashboard/view.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
            "flash_message": flash_message,
            "flash_type": flash_type,
        },
    )
