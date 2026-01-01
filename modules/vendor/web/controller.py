# Python Standard Library Imports
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from modules.address_type.business.service import AddressTypeService
from modules.vendor_type.business.service import VendorTypeService
from modules.vendor.business.service import VendorService
from modules.auth.business.service import get_current_user_web as get_current_vendor_web

router = APIRouter(prefix="/vendor", tags=["web", "vendor"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_vendors(request: Request, current_user: dict = Depends(get_current_vendor_web)):
    """
    List all vendors.
    """
    vendors = VendorService().read_all()
    return templates.TemplateResponse(
        "vendor/list.html",
        {
            "request": request,
            "vendors": [vendor.to_dict() for vendor in vendors],
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/create")
async def create_vendor(request: Request, current_user: dict = Depends(get_current_vendor_web)):
    """
    Render create vendor form.
    """
    try:
        vendor_types = VendorTypeService().read_all()
        address_types = AddressTypeService().read_all()
        return templates.TemplateResponse(
            "vendor/create.html",
            {
                "request": request,
                "vendor_types": vendor_types,
                "address_types": address_types,
                "current_user": current_user,
                "current_path": request.url.path,
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{public_id}")
async def view_vendor(request: Request, public_id: str, current_user: dict = Depends(get_current_vendor_web)):
    """
    View a vendor.
    """
    try:
        vendor = VendorService().read_by_public_id(public_id=public_id)
        return templates.TemplateResponse(
            "vendor/view.html",
            {
                "request": request,
                "vendor": vendor.to_dict(),
                "current_user": current_user,
                "current_path": request.url.path,
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{public_id}/edit")
async def edit_vendor(request: Request, public_id: str, current_user: dict = Depends(get_current_vendor_web)):
    """
    Edit a vendor.
    """
    try:
        vendor = VendorService().read_by_public_id(public_id=public_id)
        return templates.TemplateResponse(
            "vendor/edit.html",
            {
                "request": request,
                "vendor": vendor.to_dict(),
                "current_user": current_user,
                "current_path": request.url.path,
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
