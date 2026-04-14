# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.cost_code.business.service import CostCodeService
from shared.rbac import require_module_web
from shared.rbac_constants import Modules
from integrations.intuit.qbo.item.connector.sub_cost_code.business.service import ItemSubCostCodeConnector
from integrations.intuit.qbo.item.business.service import QboItemService

router = APIRouter(prefix="/sub-cost-code", tags=["web", "sub-cost-code"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_sub_cost_codes(request: Request, current_user: dict = Depends(require_module_web(Modules.COST_CODES))):
    """
    Get all sub cost codes.
    """
    sub_cost_codes = SubCostCodeService().read_all()
    return templates.TemplateResponse(
        "sub_cost_code/list.html",
        {
            "request": request,
            "sub_cost_codes": sub_cost_codes,
            "current_user": current_user,
            "current_path": request.url.path,
        }
    )


@router.get("/create")
async def create_sub_cost_code(request: Request, current_user: dict = Depends(require_module_web(Modules.COST_CODES))):
    """
    Create a sub cost code.
    """
    cost_codes = CostCodeService().read_all()
    return templates.TemplateResponse(
        "sub_cost_code/create.html",
        {
            "request": request,
            "cost_codes": cost_codes,
            "current_user": current_user,
            "current_path": request.url.path,
        }
    )


@router.get("/{public_id}")
async def view_sub_cost_code(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.COST_CODES))):
    """
    View a sub cost code.
    """
    sub_cost_code = SubCostCodeService().read_by_public_id(public_id=public_id)
    cost_code = CostCodeService().read_by_id(id=sub_cost_code.cost_code_id)

    # Load linked QBO Item if mapping exists
    qbo_item = None
    qbo_mapping = ItemSubCostCodeConnector().get_mapping_by_sub_cost_code_id(sub_cost_code.id)
    if qbo_mapping:
        qbo_item = QboItemService().read_by_id(qbo_mapping.qbo_item_id)

    return templates.TemplateResponse(
        "sub_cost_code/view.html",
        {
            "request": request,
            "sub_cost_code": sub_cost_code.to_dict(),
            "cost_code": cost_code.to_dict(),
            "qbo_item": qbo_item.to_dict() if qbo_item else None,
            "current_user": current_user,
            "current_path": request.url.path,
        }
    )


@router.get("/{public_id}/edit")
async def edit_sub_cost_code(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.COST_CODES))):
    """
    Edit a sub cost code.
    """
    cost_codes = CostCodeService().read_all()
    sub_cost_code = SubCostCodeService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "sub_cost_code/edit.html",
        {
            "request": request,
            "sub_cost_code": sub_cost_code.to_dict(),
            "cost_codes": cost_codes,
            "current_user": current_user,
            "current_path": request.url.path,
        }
    )
