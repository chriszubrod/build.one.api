# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from modules.module.business.service import ModuleService
from modules.auth.business.service import get_current_user_web

router = APIRouter(prefix="/module", tags=["web", "module"])
templates = Jinja2Templates(directory="templates/module")


@router.get("/list")
async def list_modules(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    List all modules.
    """
    modules = ModuleService().read_all()
    return templates.TemplateResponse(
        "list.html",
        {
            "request": request,
            "modules": modules,
            "current_user": current_user,
        },
    )


@router.get("/create")
async def create_module(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Render create module form.
    """
    return templates.TemplateResponse(
        "create.html",
        {
            "request": request,
            "current_user": current_user,
        },
    )


@router.get("/{public_id}")
async def view_module(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    View a module.
    """
    module = ModuleService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "view.html",
        {
            "request": request,
            "module": module.to_dict(),
            "current_user": current_user,
        },
    )


@router.get("/{public_id}/edit")
async def edit_module(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    Edit a module.
    """
    module = ModuleService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "module": module.to_dict(),
            "current_user": current_user,
        },
    )
