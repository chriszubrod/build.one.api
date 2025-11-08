# Python Standard Library Imports
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from modules.vendor_type.business.service import VendorTypeService
from modules.auth.business.service import get_current_user_web as get_current_vendor_type_web

router = APIRouter(prefix="/vendor-type", tags=["web", "vendor_type"])
templates = Jinja2Templates(directory="templates/vendor_type")


@router.get("/list")
async def list_vendor_types(request: Request, current_user: dict = Depends(get_current_vendor_type_web)):
    """
    List all vendor types.
    """
    vendor_types = VendorTypeService().read_all()
    return templates.TemplateResponse(
        "list.html",
        {
            "request": request,
            "vendor_types": vendor_types,
            "current_user": current_user,
        },
    )


@router.get("/create")
async def create_vendor_type(request: Request, current_user: dict = Depends(get_current_vendor_type_web)):
    """
    Render create vendor type form.
    """
    return templates.TemplateResponse(
        "create.html",
        {
            "request": request,
            "current_user": current_user,
        },
    )


@router.get("/{public_id}")
async def view_vendor_type(request: Request, public_id: str, current_user: dict = Depends(get_current_vendor_type_web)):
    """
    View a vendor type.
    """
    vendor_type = VendorTypeService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "view.html",
        {
            "request": request,
            "vendor_type": vendor_type.to_dict(),
            "current_user": current_user,
        },
    )


@router.get("/{public_id}/edit")
async def edit_vendor_type(request: Request, public_id: str, current_user: dict = Depends(get_current_vendor_type_web)):
    """
    Edit a vendor type.
    """
    vendor_type = VendorTypeService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "vendor_type": vendor_type.to_dict(),
            "current_user": current_user,
        },
    )
