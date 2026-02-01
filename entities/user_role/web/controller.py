# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.user_role.business.service import UserRoleService
from entities.auth.business.service import get_current_user_web

router = APIRouter(prefix="/user_role", tags=["web", "user_role"])
templates = Jinja2Templates(directory="templates/user_role")


@router.get("/list")
async def list_user_roles(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    List all user roles.
    """
    user_roles = UserRoleService().read_all()
    return templates.TemplateResponse(
        "list.html",
        {
            "request": request,
            "user_roles": user_roles,
            "current_user": current_user
        },
    )


@router.get("/create")
async def create_user_role(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Render create user role form.
    """
    return templates.TemplateResponse(
        "create.html",
        {
            "request": request,
            "current_user": current_user
        },
    )


@router.get("/{public_id}")
async def view_user_role(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    View a user role.
    """
    user_role = UserRoleService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "view.html",
        {
            "request": request,
            "user_role": user_role.to_dict(),
            "current_user": current_user
        },
    )


@router.get("/{public_id}/edit")
async def edit_user_role(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    Edit a user role.
    """
    user_role = UserRoleService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "user_role": user_role.to_dict(),
            "current_user": current_user
        },
    )
