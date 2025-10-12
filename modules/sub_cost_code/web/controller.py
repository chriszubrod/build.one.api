# Python Standard Library Imports
from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from modules.sub_cost_code.business.service import SubCostCodeService

router = APIRouter(prefix="/sub-cost-code", tags=["web", "sub-cost-code"])
service = SubCostCodeService()
templates = Jinja2Templates(directory="templates/sub_cost_code")


@router.get("/list")
async def list_sub_cost_codes(request: Request, cost_code_public_id: str | None = None):
    """
    Get all sub cost codes.
    """
    sub_cost_codes = service.read_all(cost_code_public_id=cost_code_public_id)
    return templates.TemplateResponse(
        "list.html",
        {
            "request": request,
            "sub_cost_codes": sub_cost_codes,
            "cost_code_public_id": cost_code_public_id,
        }
    )


@router.get("/create")
async def create_sub_cost_code(request: Request, cost_code_public_id: str | None = None):
    """
    Create a sub cost code.
    """
    return templates.TemplateResponse(
        "create.html",
        {
            "request": request,
            "cost_code_public_id": cost_code_public_id,
        }
    )


@router.get("/{public_id}")
async def view_sub_cost_code(request: Request, public_id: str):
    """
    View a sub cost code.
    """
    sub_cost_code = service.read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "view.html",
        {
            "request": request,
            "sub_cost_code": sub_cost_code.to_dict(),
        }
    )


@router.get("/{public_id}/edit")
async def edit_sub_cost_code(request: Request, public_id: str):
    """
    Edit a sub cost code.
    """
    sub_cost_code = service.read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "sub_cost_code": sub_cost_code.to_dict(),
        }
    )
