# Python Standard Library Imports
import logging
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse

# Third-party Imports

# Local Imports
from modules.user.business.service import UserService
from modules.auth.business.service import get_current_user_web
from modules.module.business.service import ModuleService

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
    _modules = []
    _organizations = []
    _projects = []
    flash_message = None
    flash_type = None


    # Read all modules
    try:
        _modules = ModuleService().read_all()
    except Exception as e:
        logger.error(f"Error reading modules: {e}")
        flash_message = "Error reading modules"
        flash_type = "error"

    # TODO: Read all organizations

    # TODO: Read all projects

    return templates.TemplateResponse(
        "dashboard/view.html",
        {
            "request": request,
            "current_user": current_user,
            "modules": _modules,
            "current_path": request.url.path,
            "flash_message": flash_message,
            "flash_type": flash_type,
        },
    )
