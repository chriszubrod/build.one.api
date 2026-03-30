# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.user_role.business.service import UserRoleService
from entities.user.business.service import UserService
from entities.role.business.service import RoleService
from shared.rbac import require_module_web
from shared.rbac_constants import Modules

router = APIRouter(prefix="/user_role", tags=["web", "user_role"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_user_roles(request: Request, current_user: dict = Depends(require_module_web(Modules.ROLES))):
    """
    List all user roles.
    """
    user_roles = UserRoleService().read_all()
    users = UserService().read_all()
    roles = RoleService().read_all()
    user_map = {u.id: f"{u.firstname} {u.lastname or ''}".strip() for u in users}
    role_map = {r.id: r.name for r in roles}
    return templates.TemplateResponse(
        "user_role/list.html",
        {
            "request": request,
            "user_roles": user_roles,
            "user_map": user_map,
            "role_map": role_map,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/create")
async def create_user_role(request: Request, current_user: dict = Depends(require_module_web(Modules.ROLES, "can_create"))):
    """
    Render create user role form.
    """
    users = UserService().read_all()
    roles = RoleService().read_all()
    return templates.TemplateResponse(
        "user_role/create.html",
        {
            "request": request,
            "users": [u.to_dict() for u in users],
            "roles": [r.to_dict() for r in roles],
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}")
async def view_user_role(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.ROLES))):
    """
    View a user role.
    """
    user_role = UserRoleService().read_by_public_id(public_id=public_id)
    users = UserService().read_all()
    roles = RoleService().read_all()
    user_map = {u.id: f"{u.firstname} {u.lastname or ''}".strip() for u in users}
    role_map = {r.id: r.name for r in roles}
    return templates.TemplateResponse(
        "user_role/view.html",
        {
            "request": request,
            "user_role": user_role.to_dict(),
            "user_map": user_map,
            "role_map": role_map,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_user_role(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.ROLES, "can_update"))):
    """
    Edit a user role.
    """
    user_role = UserRoleService().read_by_public_id(public_id=public_id)
    users = UserService().read_all()
    roles = RoleService().read_all()
    return templates.TemplateResponse(
        "user_role/edit.html",
        {
            "request": request,
            "user_role": user_role.to_dict(),
            "users": [u.to_dict() for u in users],
            "roles": [r.to_dict() for r in roles],
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
