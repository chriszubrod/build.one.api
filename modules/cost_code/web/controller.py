# Python Standard Library Imports
from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from modules.cost_code.business.service import CostCodeService

router = APIRouter(prefix="/cost-code", tags=["web", "cost-code"])
service = CostCodeService()
templates = Jinja2Templates(directory="templates/cost_code")


@router.get("/list")
async def list_cost_codes(request: Request):
    """
    Get all cost codes.
    """
    cost_codes = service.read_all()
    return templates.TemplateResponse(
        "list.html",
        {
            "request": request,
            "cost_codes": cost_codes,
        }
    )


@router.get("/create")
async def create_cost_code(request: Request):
    """
    Create a cost code.
    """
    return templates.TemplateResponse(
        "create.html",
        {
            "request": request,
        }
    )


@router.get("/{public_id}")
async def view_cost_code(request: Request, public_id: str):
    """
    View a cost code.
    """
    cost_code = service.read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "view.html",
        {
            "request": request,
            "cost_code": cost_code.to_dict(),
        }
    )


@router.get("/{public_id}/edit")
async def edit_cost_code(request: Request, public_id: str):
    """
    Edit a cost code.
    """
    cost_code = service.read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "cost_code": cost_code.to_dict(),
        }
    )
