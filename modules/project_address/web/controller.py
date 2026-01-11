# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from modules.project_address.business.service import ProjectAddressService
from modules.auth.business.service import get_current_user_web

router = APIRouter(prefix="/project_address", tags=["web", "project_address"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_project_addresses(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    List all project addresses.
    """
    project_addresses = ProjectAddressService().read_all()
    return templates.TemplateResponse(
        "project_address/list.html",
        {
            "request": request,
            "project_addresses": [project_address.to_dict() for project_address in project_addresses],
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/create")
async def create_project_address(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Render create project address form.
    """
    return templates.TemplateResponse(
        "project_address/create.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}")
async def view_project_address(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    View a project address.
    """
    project_address = ProjectAddressService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "project_address/view.html",
        {
            "request": request,
            "project_address": project_address.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_project_address(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    Edit a project address.
    """
    project_address = ProjectAddressService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "project_address/edit.html",
        {
            "request": request,
            "project_address": project_address.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
