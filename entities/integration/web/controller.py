# Python Standard Library Imports
from urllib.parse import quote

# Third-party Imports
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

# Local Imports
from entities.integration.business.service import IntegrationService
from entities.auth.business.service import get_current_user_web

router = APIRouter(prefix="/integration", tags=["web", "integration"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_integrations(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    List all integrations.
    """
    integrations = IntegrationService().read_all()
    integrations_list = [integration.to_dict() for integration in integrations] if integrations else []
    return templates.TemplateResponse(
        "integration/list.html",
        {
            "request": request,
            "integrations": integrations_list,
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


@router.get("/disconnect/callback")
async def disconnect_callback(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Generic disconnect callback route for all integration types.
    Handles redirects from external services (e.g., Intuit) after disconnect.
    Redirects to the integration list page with optional success/error message.
    Query parameters:
    - success: 'true' or 'false' (optional)
    - message: Success or error message (optional)
    - integration_id: Public ID of the integration (optional)
    """
    success = request.query_params.get("success", "true").lower() == "true"
    message = request.query_params.get("message", "")
    
    # Build redirect URL with query parameters for success/error message
    redirect_url = "/integration/list"
    if message:
        encoded_message = quote(message)
        if success:
            redirect_url += f"?success=true&message={encoded_message}"
        else:
            redirect_url += f"?success=false&message={encoded_message}"
    elif success:
        redirect_url += "?success=true"
    
    return RedirectResponse(url=redirect_url)
