# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from modules.integration.business.service import IntegrationService
from modules.auth.business.service import get_current_user_web

router = APIRouter(prefix="/integration", tags=["web", "integration"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_integrations(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    List all integrations.
    """
    integrations = IntegrationService().read_all()
    return templates.TemplateResponse(
        "integration/list.html",
        {
            "request": request,
            "integrations": integrations,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/create")
async def create_integration(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Render create integration form.
    """
    return templates.TemplateResponse(
        "integration/create.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}")
async def view_integration(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    View a integration.
    """
    integration = IntegrationService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "integration/view.html",
        {
            "request": request,
            "integration": integration.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_integration(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    Edit a integration.
    """
    integration = IntegrationService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "integration/edit.html",
        {
            "request": request,
            "integration": integration.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
