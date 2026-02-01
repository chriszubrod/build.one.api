# Python Standard Library Imports
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.vendor.business.service import QboVendorService
from services.auth.business.service import get_current_user_web as get_current_qbo_vendor_web

router = APIRouter(prefix="/qbo-vendor", tags=["web", "qbo-vendor"])
templates = Jinja2Templates(directory="templates/qbo-vendor")


@router.get("/list")
async def list_qbo_vendors(request: Request, current_user: dict = Depends(get_current_qbo_vendor_web)):
    """
    List all QBO vendors.
    """
    vendors = QboVendorService().read_all()
    return templates.TemplateResponse(
        "list.html",
        {
            "request": request,
            "qbo_vendors": vendors,
            "current_user": current_user,
        },
    )


@router.get("/create")
async def create_qbo_vendor(request: Request, current_user: dict = Depends(get_current_qbo_vendor_web)):
    """
    Render create QBO vendor form.
    """
    return templates.TemplateResponse(
        "create.html",
        {
            "request": request,
            "current_user": current_user,
        },
    )


@router.get("/{id}")
async def view_qbo_vendor(request: Request, id: str, current_user: dict = Depends(get_current_qbo_vendor_web)):
    """
    View a QBO vendor.
    """
    vendor = QboVendorService().read_by_id(id=id)
    return templates.TemplateResponse(
        "view.html",
        {
            "request": request,
            "qbo_vendor": vendor.to_dict(),
            "current_user": current_user,
        },
    )


@router.get("/{id}/edit")
async def edit_qbo_vendor(request: Request, id: str, current_user: dict = Depends(get_current_qbo_vendor_web)):
    """
    Edit a QBO vendor.
    """
    vendor = QboVendorService().read_by_id(id=id)
    return templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "qbo_vendor": vendor.to_dict(),
            "current_user": current_user,
        },
    )
