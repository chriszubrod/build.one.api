# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.role.business.service import RoleService
from entities.module.business.service import ModuleService
from entities.role_module.business.service import RoleModuleService
from shared.rbac import require_module_web
from shared.rbac_constants import Modules

router = APIRouter(prefix="/role", tags=["web", "role"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_roles(request: Request, current_user: dict = Depends(require_module_web(Modules.ROLES))):
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
async def create_role(request: Request, current_user: dict = Depends(require_module_web(Modules.ROLES, "can_create"))):
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
async def view_role(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.ROLES))):
    """
    View a role.
    """
    role = RoleService().read_by_public_id(public_id=public_id)
    role_modules = RoleModuleService().read_all_by_role_id(role_id=role.id)
    modules = ModuleService().read_all()
    module_map = {m.id: m.name for m in modules}
    return templates.TemplateResponse(
        "role/view.html",
        {
            "request": request,
            "role": role.to_dict(),
            "role_modules": [rm.to_dict() for rm in role_modules],
            "module_map": module_map,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_role(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.ROLES, "can_update"))):
    """
    Edit a role.
    """
    role = RoleService().read_by_public_id(public_id=public_id)
    role_modules = RoleModuleService().read_all_by_role_id(role_id=role.id)
    modules = ModuleService().read_all()
    module_map = {m.id: m.name for m in modules}
    # Modules not yet assigned to this role (for the add dropdown)
    assigned_module_ids = {rm.module_id for rm in role_modules}
    available_modules = [m for m in modules if m.id not in assigned_module_ids]
    return templates.TemplateResponse(
        "role/edit.html",
        {
            "request": request,
            "role": role.to_dict(),
            "role_modules": [rm.to_dict() for rm in role_modules],
            "module_map": module_map,
            "available_modules": [m.to_dict() for m in available_modules],
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
