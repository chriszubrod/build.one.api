# Python Standard Library Imports
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from modules.address_type.business.service import AddressTypeService
from modules.vendor_type.business.service import VendorTypeService
from modules.vendor.business.service import VendorService
from modules.taxpayer.business.service import TaxpayerService
from modules.address.business.service import AddressService
from modules.vendor_address.business.service import VendorAddressService
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
        if not vendor:
            raise HTTPException(status_code=404, detail="Vendor not found")
        
        vendor_dict = vendor.to_dict()
        
        # Fetch related data
        taxpayer = None
        if vendor.taxpayer_id:
            taxpayer = TaxpayerService().read_by_id(id=vendor.taxpayer_id)
        
        vendor_type = None
        if vendor.vendor_type_id:
            vendor_type = VendorTypeService().read_by_id(id=str(vendor.vendor_type_id))
        
        # Get all vendor addresses for this vendor
        all_vendor_addresses = VendorAddressService().read_all()
        vendor_addresses = [va for va in all_vendor_addresses if va.vendor_id and int(va.vendor_id) == vendor.id]
        
        # Get addresses and address types
        address_types = AddressTypeService().read_all()
        addresses_by_type = {}
        
        for va in vendor_addresses:
            if va.address_id and va.address_type_id:
                address = AddressService().read_by_id(id=va.address_id)
                address_type_id = int(va.address_type_id)
                addresses_by_type[address_type_id] = address
        
        # Get all address types for display
        all_address_types = AddressTypeService().read_all()
        
        return templates.TemplateResponse(
            "vendor/view.html",
            {
                "request": request,
                "vendor": vendor_dict,
                "taxpayer": taxpayer.to_dict() if taxpayer else None,
                "vendor_type": vendor_type.to_dict() if vendor_type else None,
                "address_types": all_address_types,
                "addresses_by_type": addresses_by_type,
                "current_user": current_user,
                "current_path": request.url.path,
            },
        )
    except HTTPException:
        raise
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
