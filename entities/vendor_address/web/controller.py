# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.vendor_address.business.service import VendorAddressService
from shared.rbac import require_module_web
from shared.rbac_constants import Modules

router = APIRouter(prefix="/vendor_address", tags=["web", "vendor_address"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_vendor_addresses(request: Request, current_user: dict = Depends(require_module_web(Modules.VENDORS))):
    """
    List all vendor addresses.
    """
    vendor_addresses = VendorAddressService().read_all()
    return templates.TemplateResponse(
        "vendor_address/list.html",
        {
            "request": request,
            "vendor_addresses": [vendor_address.to_dict() for vendor_address in vendor_addresses],
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/create")
async def create_vendor_address(request: Request, current_user: dict = Depends(require_module_web(Modules.VENDORS))):
    """
    Render create vendor address form.
    """
    return templates.TemplateResponse(
        "vendor_address/create.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}")
async def view_vendor_address(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.VENDORS))):
    """
    View a vendor address.
    """
    vendor_address = VendorAddressService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "vendor_address/view.html",
        {
            "request": request,
            "vendor_address": vendor_address.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_vendor_address(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.VENDORS))):
    """
    Edit a vendor address.
    """
    vendor_address = VendorAddressService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "vendor_address/edit.html",
        {
            "request": request,
            "vendor_address": vendor_address.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
