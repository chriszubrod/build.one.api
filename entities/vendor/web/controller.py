# Python Standard Library Imports
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.address_type.business.service import AddressTypeService
from entities.vendor_type.business.service import VendorTypeService
from entities.vendor.business.service import VendorService
from entities.taxpayer.business.service import TaxpayerService
from entities.address.business.service import AddressService
from entities.vendor_address.business.service import VendorAddressService
from entities.taxpayer_attachment.business.service import TaxpayerAttachmentService
from entities.attachment.business.service import AttachmentService
from entities.auth.business.service import get_current_user_web as get_current_vendor_web

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
        
        # Create a map of address_type database ID to public_id
        address_type_id_to_public_id = {}
        for at in address_types:
            if at.id:
                address_type_id_to_public_id[int(at.id)] = at.public_id
        
        for va in vendor_addresses:
            if va.address_id and va.address_type_id:
                address = AddressService().read_by_id(id=va.address_id)
                address_type_id = int(va.address_type_id)
                address_type_public_id = address_type_id_to_public_id.get(address_type_id)
                if address_type_public_id:
                    addresses_by_type[address_type_public_id] = address
        
        # Get all address types for display
        all_address_types = AddressTypeService().read_all()
        
        # Fetch taxpayer attachments if taxpayer exists
        taxpayer_attachments = []
        attachments_data = []
        seen_attachment_ids = set()  # Track unique attachment IDs to prevent duplicates
        if taxpayer:
            taxpayer_attachment_service = TaxpayerAttachmentService()
            attachment_service = AttachmentService()
            taxpayer_attachments = taxpayer_attachment_service.read_by_taxpayer_id(taxpayer_public_id=taxpayer.public_id)
            
            # Get full attachment details for each taxpayer attachment
            # Deduplicate by attachment_id to handle duplicate TaxpayerAttachment records
            for ta in taxpayer_attachments:
                if ta.attachment_id and ta.attachment_id not in seen_attachment_ids:
                    attachment = attachment_service.read_by_id(id=ta.attachment_id)
                    if attachment:
                        seen_attachment_ids.add(ta.attachment_id)
                        attachments_data.append({
                            "taxpayer_attachment": ta.to_dict(),
                            "attachment": attachment.to_dict()
                        })
        
        return templates.TemplateResponse(
            "vendor/view.html",
            {
                "request": request,
                "vendor": vendor_dict,
                "taxpayer": taxpayer.to_dict() if taxpayer else None,
                "vendor_type": vendor_type.to_dict() if vendor_type else None,
                "address_types": all_address_types,
                "addresses_by_type": addresses_by_type,
                "attachments": attachments_data,
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
        vendor_addresses_by_type = {}
        
        # Create a map of address_type database ID to public_id
        address_type_id_to_public_id = {}
        for at in address_types:
            if at.id:
                address_type_id_to_public_id[int(at.id)] = at.public_id
        
        for va in vendor_addresses:
            if va.address_id and va.address_type_id:
                address = AddressService().read_by_id(id=va.address_id)
                if address:
                    address_type_id = int(va.address_type_id)
                    address_type_public_id = address_type_id_to_public_id.get(address_type_id)
                    if address_type_public_id:
                        addresses_by_type[address_type_public_id] = address.to_dict()
                        vendor_addresses_by_type[address_type_public_id] = va.to_dict()
        
        # Get all address types and vendor types for dropdowns
        all_address_types = AddressTypeService().read_all()
        vendor_types = VendorTypeService().read_all()
        
        # Fetch taxpayer attachments if taxpayer exists
        taxpayer_attachments = []
        attachments_data = []
        seen_attachment_ids = set()  # Track unique attachment IDs to prevent duplicates
        if taxpayer:
            taxpayer_attachment_service = TaxpayerAttachmentService()
            attachment_service = AttachmentService()
            taxpayer_attachments = taxpayer_attachment_service.read_by_taxpayer_id(taxpayer_public_id=taxpayer.public_id)
            
            # Get full attachment details for each taxpayer attachment
            # Deduplicate by attachment_id to handle duplicate TaxpayerAttachment records
            for ta in taxpayer_attachments:
                if ta.attachment_id and ta.attachment_id not in seen_attachment_ids:
                    attachment = attachment_service.read_by_id(id=ta.attachment_id)
                    if attachment:
                        seen_attachment_ids.add(ta.attachment_id)
                        attachments_data.append({
                            "taxpayer_attachment": ta.to_dict(),
                            "attachment": attachment.to_dict()
                        })
        
        return templates.TemplateResponse(
            "vendor/edit.html",
            {
                "request": request,
                "vendor": vendor_dict,
                "taxpayer": taxpayer.to_dict() if taxpayer else None,
                "vendor_type": vendor_type.to_dict() if vendor_type else None,
                "vendor_types": vendor_types,
                "address_types": all_address_types,
                "addresses_by_type": addresses_by_type,
                "vendor_addresses_by_type": vendor_addresses_by_type,
                "attachments": attachments_data,
                "current_user": current_user,
                "current_path": request.url.path,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
