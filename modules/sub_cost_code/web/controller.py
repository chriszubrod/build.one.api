# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from modules.sub_cost_code.business.service import SubCostCodeService
from modules.cost_code.business.service import CostCodeService
from modules.auth.business.service import get_current_user_web

router = APIRouter(prefix="/sub-cost-code", tags=["web", "sub-cost-code"])
templates = Jinja2Templates(directory="templates/sub_cost_code")


@router.get("/list")
async def list_sub_cost_codes(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Get all sub cost codes.
    """
    sub_cost_codes = SubCostCodeService().read_all()
    return templates.TemplateResponse(
        "list.html",
        {
            "request": request,
            "sub_cost_codes": sub_cost_codes,
            "current_user": current_user,
        }
    )


@router.get("/create")
async def create_sub_cost_code(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Create a sub cost code.
    """
    cost_codes = CostCodeService().read_all()
    return templates.TemplateResponse(
        "create.html",
        {
            "request": request,
            "cost_codes": cost_codes,
            "current_user": current_user
        }
    )


@router.get("/{public_id}")
async def view_sub_cost_code(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    View a sub cost code.
    """
    sub_cost_code = SubCostCodeService().read_by_public_id(public_id=public_id)
    cost_code = CostCodeService().read_by_public_id(public_id=sub_cost_code.cost_code_id)
    return templates.TemplateResponse(
        "view.html",
        {
            "request": request,
            "sub_cost_code": sub_cost_code.to_dict(),
            "cost_code": cost_code.to_dict(),
            "current_user": current_user,
        }
    )


@router.get("/{public_id}/edit")
async def edit_sub_cost_code(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    Edit a sub cost code.
    """
    cost_codes = CostCodeService().read_all()
    sub_cost_code = SubCostCodeService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "sub_cost_code": sub_cost_code.to_dict(),
            "cost_codes": cost_codes,
            "current_user": current_user,
        }
    )
