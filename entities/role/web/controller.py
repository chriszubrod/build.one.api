# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.role.business.service import RoleService
from entities.auth.business.service import get_current_user_web

router = APIRouter(prefix="/role", tags=["web", "role"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_roles(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    List all roles.
    """
    roles = RoleService().read_all()
    return templates.TemplateResponse(
        "role/list.html",
        {
            "request": request,
            "roles": roles,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/create")
async def create_role(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Render create role form.
    """
    return templates.TemplateResponse(
        "role/create.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}")
async def view_role(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    View a role.
    """
    role = RoleService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "role/view.html",
        {
            "request": request,
            "role": role.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_role(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    Edit a role.
    """
    role = RoleService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "role/edit.html",
        {
            "request": request,
            "role": role.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
