# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.role_module.business.service import RoleModuleService
from entities.role.business.service import RoleService
from entities.module.business.service import ModuleService
from shared.rbac import require_module_web
from shared.rbac_constants import Modules

router = APIRouter(prefix="/role_module", tags=["web", "role_module"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_role_modules(request: Request, current_user: dict = Depends(require_module_web(Modules.ROLES))):
    """
    List all role modules.
    """
    role_modules = RoleModuleService().read_all()
    roles = RoleService().read_all()
    modules = ModuleService().read_all()
    role_map = {r.id: r.name for r in roles}
    module_map = {m.id: m.name for m in modules}
    return templates.TemplateResponse(
        "role_module/list.html",
        {
            "request": request,
            "role_modules": role_modules,
            "role_map": role_map,
            "module_map": module_map,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/create")
async def create_role_module(request: Request, current_user: dict = Depends(require_module_web(Modules.ROLES, "can_create"))):
    """
    Render create role module form.
    """
    roles = RoleService().read_all()
    modules = ModuleService().read_all()
    return templates.TemplateResponse(
        "role_module/create.html",
        {
            "request": request,
            "roles": [r.to_dict() for r in roles],
            "modules": [m.to_dict() for m in modules],
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}")
async def view_role_module(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.ROLES))):
    """
    View a role module.
    """
    role_module = RoleModuleService().read_by_public_id(public_id=public_id)
    roles = RoleService().read_all()
    modules = ModuleService().read_all()
    role_map = {r.id: r.name for r in roles}
    module_map = {m.id: m.name for m in modules}
    return templates.TemplateResponse(
        "role_module/view.html",
        {
            "request": request,
            "role_module": role_module.to_dict(),
            "role_map": role_map,
            "module_map": module_map,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_role_module(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.ROLES, "can_update"))):
    """
    Edit a role module.
    """
    role_module = RoleModuleService().read_by_public_id(public_id=public_id)
    roles = RoleService().read_all()
    modules = ModuleService().read_all()
    return templates.TemplateResponse(
        "role_module/edit.html",
        {
            "request": request,
            "role_module": role_module.to_dict(),
            "roles": [r.to_dict() for r in roles],
            "modules": [m.to_dict() for m in modules],
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
