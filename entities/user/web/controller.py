# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.user.business.service import UserService
from entities.role.business.service import RoleService
from entities.user_role.business.service import UserRoleService
from entities.contact.business.service import ContactService
from shared.rbac import require_module_web
from shared.rbac_constants import Modules

router = APIRouter(prefix="/user", tags=["web", "user"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_users(request: Request, current_user: dict = Depends(require_module_web(Modules.USERS))):
    """
    List all users.
    """
    users = UserService().read_all()
    return templates.TemplateResponse(
        "user/list.html",
        {
            "request": request,
            "users": users,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/create")
async def create_user(request: Request, current_user: dict = Depends(require_module_web(Modules.USERS, "can_create"))):
    """
    Render create user form.
    """
    roles = RoleService().read_all()
    return templates.TemplateResponse(
        "user/create.html",
        {
            "request": request,
            "roles": [r.to_dict() for r in roles],
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}")
async def view_user(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.USERS))):
    """
    View a user.
    """
    user = UserService().read_by_public_id(public_id=public_id)
    user_role = UserRoleService().read_by_user_id(user_id=user.id)
    role_name = None
    if user_role:
        role = RoleService().read_by_id(id=user_role.role_id)
        if role:
            role_name = role.name
    contacts = ContactService().read_by_user_id(user_id=user.id)
    return templates.TemplateResponse(
        "user/view.html",
        {
            "request": request,
            "user": user.to_dict(),
            "role_name": role_name,
            "contacts": [c.to_dict() for c in contacts],
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_user(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.USERS, "can_update"))):
    """
    Edit a user.
    """
    user = UserService().read_by_public_id(public_id=public_id)
    roles = RoleService().read_all()
    user_role = UserRoleService().read_by_user_id(user_id=user.id)
    contacts = ContactService().read_by_user_id(user_id=user.id)
    return templates.TemplateResponse(
        "user/edit.html",
        {
            "request": request,
            "user": user.to_dict(),
            "roles": [r.to_dict() for r in roles],
            "user_role": user_role.to_dict() if user_role else None,
            "contacts": [c.to_dict() for c in contacts],
            "parent_entity": "user",
            "parent_id": user.id,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
