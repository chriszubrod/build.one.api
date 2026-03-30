# Python Standard Library Imports
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.taxpayer.business.service import TaxpayerService
from shared.rbac import require_module_web
from shared.rbac_constants import Modules

router = APIRouter(prefix="/taxpayer", tags=["web", "taxpayer"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_taxpayers(request: Request, current_user: dict = Depends(require_module_web(Modules.VENDORS))):
    """
    List all taxpayers.
    """
    taxpayers = TaxpayerService().read_all()
    return templates.TemplateResponse(
        "taxpayer/list.html",
        {
            "request": request,
            "taxpayers": taxpayers,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/create")
async def create_taxpayer(request: Request, current_user: dict = Depends(require_module_web(Modules.VENDORS, "can_create"))):
    """
    Render create taxpayer form.
    """
    return templates.TemplateResponse(
        "taxpayer/create.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}")
async def view_taxpayer(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.VENDORS))):
    """
    View a taxpayer.
    """
    taxpayer = TaxpayerService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "taxpayer/view.html",
        {
            "request": request,
            "taxpayer": taxpayer.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_taxpayer(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.VENDORS, "can_update"))):
    """
    Edit a taxpayer.
    """
    taxpayer = TaxpayerService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "taxpayer/edit.html",
        {
            "request": request,
            "taxpayer": taxpayer.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
