# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.user_module.business.service import UserModuleService
from entities.user.business.service import UserService
from entities.module.business.service import ModuleService
from shared.rbac import require_module_web
from shared.rbac_constants import Modules

router = APIRouter(prefix="/user_module", tags=["web", "user_module"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_user_modules(request: Request, current_user: dict = Depends(require_module_web(Modules.ROLES))):
    """
    List all user modules.
    """
    user_modules = UserModuleService().read_all()
    users = UserService().read_all()
    modules = ModuleService().read_all()
    user_map = {u.id: f"{u.firstname} {u.lastname or ''}".strip() for u in users}
    module_map = {m.id: m.name for m in modules}
    return templates.TemplateResponse(
        "user_module/list.html",
        {
            "request": request,
            "user_modules": user_modules,
            "user_map": user_map,
            "module_map": module_map,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/create")
async def create_user_module(request: Request, current_user: dict = Depends(require_module_web(Modules.ROLES, "can_create"))):
    """
    Render create user module form.
    """
    users = UserService().read_all()
    modules = ModuleService().read_all()
    return templates.TemplateResponse(
        "user_module/create.html",
        {
            "request": request,
            "users": [u.to_dict() for u in users],
            "modules": [m.to_dict() for m in modules],
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}")
async def view_user_module(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.ROLES))):
    """
    View a user module.
    """
    user_module = UserModuleService().read_by_public_id(public_id=public_id)
    users = UserService().read_all()
    modules = ModuleService().read_all()
    user_map = {u.id: f"{u.firstname} {u.lastname or ''}".strip() for u in users}
    module_map = {m.id: m.name for m in modules}
    return templates.TemplateResponse(
        "user_module/view.html",
        {
            "request": request,
            "user_module": user_module.to_dict(),
            "user_map": user_map,
            "module_map": module_map,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_user_module(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.ROLES, "can_update"))):
    """
    Edit a user module.
    """
    user_module = UserModuleService().read_by_public_id(public_id=public_id)
    users = UserService().read_all()
    modules = ModuleService().read_all()
    return templates.TemplateResponse(
        "user_module/edit.html",
        {
            "request": request,
            "user_module": user_module.to_dict(),
            "users": [u.to_dict() for u in users],
            "modules": [m.to_dict() for m in modules],
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
