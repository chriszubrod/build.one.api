# Python Standard Library Imports
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from mappings.vendor_qbo_vendor.business.service import MapVendorQboVendorService
from modules.auth.business.service import get_current_user_web as get_current_vendor_qbo_vendor_web

router = APIRouter(prefix="/vendor-qbo-vendor", tags=["web", "vendor-qbo-vendor"])
templates = Jinja2Templates(directory="templates/vendor-qbo-vendor")


@router.get("/list")
async def list_vendor_qbo_vendors(request: Request, current_user: dict = Depends(get_current_vendor_qbo_vendor_web)):
    """
    List all vendor qbo vendor mapping records.
    """
    vendor_qbo_vendors = MapVendorQboVendorService().read_all()
    return templates.TemplateResponse(
        "list.html",
        {
            "request": request,
            "vendor_qbo_vendors": vendor_qbo_vendors,
            "current_user": current_user,
        },
    )


@router.get("/create")
async def create_vendor_qbo_vendor(request: Request, current_user: dict = Depends(get_current_vendor_qbo_vendor_web)):
    """
    Render create vendor qbo vendor mapping form.
    """
    return templates.TemplateResponse(
        "create.html",
        {
            "request": request,
            "current_user": current_user,
        },
    )


@router.get("/{public_id}")
async def view_vendor_qbo_vendor(request: Request, public_id: str, current_user: dict = Depends(get_current_vendor_qbo_vendor_web)):
    """
    View a vendor qbo vendor mapping record.
    """
    vendor_qbo_vendor = MapVendorQboVendorService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "view.html",
        {
            "request": request,
            "vendor_qbo_vendor": vendor_qbo_vendor.to_dict(),
            "current_user": current_user,
        },
    )


@router.get("/{public_id}/edit")
async def edit_vendor_qbo_vendor(request: Request, public_id: str, current_user: dict = Depends(get_current_vendor_qbo_vendor_web)):
    """
    Edit a vendor qbo vendor mapping record.
    """
    vendor_qbo_vendor = MapVendorQboVendorService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "vendor_qbo_vendor": vendor_qbo_vendor.to_dict(),
            "current_user": current_user,
        },
    )
