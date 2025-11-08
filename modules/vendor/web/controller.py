# Python Standard Library Imports
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from modules.vendor.business.service import VendorService
from modules.auth.business.service import get_current_user_web as get_current_vendor_web

router = APIRouter(prefix="/vendor", tags=["web", "vendor"])
templates = Jinja2Templates(directory="templates/vendor")


@router.get("/list")
async def list_vendors(request: Request, current_user: dict = Depends(get_current_vendor_web)):
    """
    List all vendors.
    """
    vendors = VendorService().read_all()
    return templates.TemplateResponse(
        "list.html",
        {
            "request": request,
            "vendors": vendors,
            "current_user": current_user,
        },
    )


@router.get("/create")
async def create_vendor(request: Request, current_user: dict = Depends(get_current_vendor_web)):
    """
    Render create vendor form.
    """
    return templates.TemplateResponse(
        "create.html",
        {
            "request": request,
            "current_user": current_user,
        },
    )


@router.get("/{public_id}")
async def view_vendor(request: Request, public_id: str, current_user: dict = Depends(get_current_vendor_web)):
    """
    View a vendor.
    """
    vendor = VendorService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "view.html",
        {
            "request": request,
            "vendor": vendor.to_dict(),
            "current_user": current_user,
        },
    )


@router.get("/{public_id}/edit")
async def edit_vendor(request: Request, public_id: str, current_user: dict = Depends(get_current_vendor_web)):
    """
    Edit a vendor.
    """
    vendor = VendorService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "vendor": vendor.to_dict(),
            "current_user": current_user,
        },
    )
