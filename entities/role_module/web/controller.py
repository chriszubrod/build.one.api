# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.role_module.business.service import RoleModuleService
from entities.auth.business.service import get_current_user_web

router = APIRouter(prefix="/role_module", tags=["web", "role_module"])
templates = Jinja2Templates(directory="templates/role_module")


@router.get("/list")
async def list_role_modules(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    List all role modules.
    """
    role_modules = RoleModuleService().read_all()
    return templates.TemplateResponse(
        "list.html",
        {
            "request": request,
            "role_modules": role_modules,
            "current_user": current_user
        },
    )


@router.get("/create")
async def create_role_module(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Render create role module form.
    """
    return templates.TemplateResponse(
        "create.html",
        {
            "request": request,
            "current_user": current_user
        },
    )


@router.get("/{public_id}")
async def view_role_module(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    View a role module.
    """
    role_module = RoleModuleService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "view.html",
        {
            "request": request,
            "role_module": role_module.to_dict(),
            "current_user": current_user
        },
    )


@router.get("/{public_id}/edit")
async def edit_role_module(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    Edit a role module.
    """
    role_module = RoleModuleService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "role_module": role_module.to_dict(),
            "current_user": current_user
        },
    )
