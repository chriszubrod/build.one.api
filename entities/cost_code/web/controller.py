# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.cost_code.business.service import CostCodeService
from shared.rbac import require_module_web
from shared.rbac_constants import Modules

router = APIRouter(prefix="/cost-code", tags=["web", "cost-code"])
templates = Jinja2Templates(directory="templates/cost_code")


@router.get("/list")
async def list_cost_codes(request: Request, current_user: dict = Depends(require_module_web(Modules.COST_CODES))):
    """
    Get all cost codes.
    """
    cost_codes = CostCodeService().read_all()
    return templates.TemplateResponse(
        "list.html",
        {
            "request": request,
            "cost_codes": cost_codes,
            "current_user": current_user,
        }
    )


@router.get("/create")
async def create_cost_code(request: Request, current_user: dict = Depends(require_module_web(Modules.COST_CODES))):
    """
    Create a cost code.
    """
    return templates.TemplateResponse(
        "create.html",
        {
            "request": request,
            "current_user": current_user,
        }
    )


@router.get("/{public_id}")
async def view_cost_code(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.COST_CODES))):
    """
    View a cost code.
    """
    cost_code = CostCodeService().read_by_public_id(public_id=public_id)
    if not cost_code:
        raise HTTPException(status_code=404, detail="Cost code not found.")
    return templates.TemplateResponse(
        "view.html",
        {
            "request": request,
            "cost_code": cost_code.to_dict(),
            "current_user": current_user,
        }
    )


@router.get("/{public_id}/edit")
async def edit_cost_code(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.COST_CODES))):
    """
    Edit a cost code.
    """
    cost_code = CostCodeService().read_by_public_id(public_id=public_id)
    if not cost_code:
        raise HTTPException(status_code=404, detail="Cost code not found.")
    return templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "cost_code": cost_code.to_dict(),
            "current_user": current_user,
        }
    )
