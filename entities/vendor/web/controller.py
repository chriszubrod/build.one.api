# Python Standard Library Imports
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.address_type.business.service import AddressTypeService
from entities.vendor_type.business.service import VendorTypeService
from entities.vendor.business.service import VendorService
from entities.contact.business.service import ContactService
from entities.taxpayer.business.service import TaxpayerService
from entities.address.business.service import AddressService
from entities.vendor_address.business.service import VendorAddressService
from entities.taxpayer_attachment.business.service import TaxpayerAttachmentService
from entities.attachment.business.service import AttachmentService
from shared.rbac import require_module_web
from shared.rbac_constants import Modules

router = APIRouter(prefix="/vendor", tags=["web", "vendor"])
templates = Jinja2Templates(directory="templates")


def _build_addresses_by_type(vendor_id: int, address_types: list) -> tuple[dict, dict]:
    """
    Fetch vendor addresses and build lookup dicts keyed by address_type public_id.

    Returns:
        (addresses_by_type, vendor_addresses_by_type) where values are dicts or model objects.
    """
    vendor_address_service = VendorAddressService()
    vendor_addresses = vendor_address_service.read_all_by_vendor_id(vendor_id=vendor_id)

    address_type_id_to_public_id = {}
    for at in address_types:
        if at.id:
            address_type_id_to_public_id[int(at.id)] = at.public_id

    address_service = AddressService()
    addresses_by_type = {}
    vendor_addresses_by_type = {}

    for va in vendor_addresses:
        if va.address_id and va.address_type_id:
            address_type_public_id = address_type_id_to_public_id.get(int(va.address_type_id))
            if address_type_public_id:
                address = address_service.read_by_id(id=va.address_id)
                if address:
                    addresses_by_type[address_type_public_id] = address
                    vendor_addresses_by_type[address_type_public_id] = va

    return addresses_by_type, vendor_addresses_by_type


@router.get("/list")
async def list_vendors(request: Request, current_user: dict = Depends(require_module_web(Modules.VENDORS))):
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
async def create_vendor(request: Request, current_user: dict = Depends(require_module_web(Modules.VENDORS))):
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
async def view_vendor(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.VENDORS))):
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
            vendor_type = VendorTypeService().read_by_id(id=int(vendor.vendor_type_id))

        # Fetch address types once and build address lookups
        address_types = AddressTypeService().read_all()
        addresses_by_type, _ = _build_addresses_by_type(vendor.id, address_types)

        # Linked SharePoint folder (for W9 backfill etc.)
        linked_folder = None
        linked_drives = []
        vendor_root_drive_public_id = None
        try:
            from integrations.ms.sharepoint.driveitem.connector.vendor.business.service import DriveItemVendorConnector
            from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository
            connector = DriveItemVendorConnector()
            linked_folder = connector.get_driveitem_for_vendor(vendor_id=int(vendor.id))
            if linked_folder:
                drive_repo = MsDriveRepository()
                ms_drive_id = linked_folder.get("ms_drive_id")
                if ms_drive_id:
                    drive = drive_repo.read_by_id(ms_drive_id)
                    if drive:
                        vendor_root_drive_public_id = drive.public_id
                from integrations.ms.sharepoint.drive.connector.company.business.service import DriveCompanyConnector
                drive_connector = DriveCompanyConnector()
                company_ids = [c.get("id") for c in current_user.get("companies", []) if c.get("id")]
                for cid in company_ids:
                    d = drive_connector.get_drive_for_company(company_id=cid)
                    if d:
                        linked_drives.append(d)
        except Exception:
            linked_drives = []

        # Fetch taxpayer attachments if taxpayer exists
        attachments_data = []
        seen_attachment_ids = set()
        if taxpayer:
            taxpayer_attachment_service = TaxpayerAttachmentService()
            attachment_service = AttachmentService()
            taxpayer_attachments = taxpayer_attachment_service.read_by_taxpayer_id(taxpayer_public_id=taxpayer.public_id)

            for ta in taxpayer_attachments:
                if ta.attachment_id and ta.attachment_id not in seen_attachment_ids:
                    attachment = attachment_service.read_by_id(id=ta.attachment_id)
                    if attachment:
                        seen_attachment_ids.add(ta.attachment_id)
                        attachments_data.append({
                            "taxpayer_attachment": ta.to_dict(),
                            "attachment": attachment.to_dict()
                        })

        contacts = ContactService().read_by_vendor_id(vendor_id=vendor.id)
        return templates.TemplateResponse(
            "vendor/view.html",
            {
                "request": request,
                "vendor": vendor_dict,
                "taxpayer": taxpayer.to_dict() if taxpayer else None,
                "vendor_type": vendor_type.to_dict() if vendor_type else None,
                "address_types": address_types,
                "addresses_by_type": addresses_by_type,
                "attachments": attachments_data,
                "linked_folder": linked_folder,
                "linked_drives": linked_drives,
                "vendor_root_drive_public_id": vendor_root_drive_public_id,
                "contacts": [c.to_dict() for c in contacts],
                "current_user": current_user,
                "current_path": request.url.path,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{public_id}/edit")
async def edit_vendor(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.VENDORS))):
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
            vendor_type = VendorTypeService().read_by_id(id=int(vendor.vendor_type_id))

        # Fetch address types once and build address lookups
        address_types = AddressTypeService().read_all()
        addresses_by_type_obj, vendor_addresses_by_type_obj = _build_addresses_by_type(vendor.id, address_types)

        # Convert to dicts for edit template
        addresses_by_type = {k: v.to_dict() for k, v in addresses_by_type_obj.items()}
        vendor_addresses_by_type = {k: v.to_dict() for k, v in vendor_addresses_by_type_obj.items()}

        # Get vendor types for dropdown
        vendor_types = VendorTypeService().read_all()

        # Fetch taxpayer attachments if taxpayer exists
        attachments_data = []
        seen_attachment_ids = set()
        if taxpayer:
            taxpayer_attachment_service = TaxpayerAttachmentService()
            attachment_service = AttachmentService()
            taxpayer_attachments = taxpayer_attachment_service.read_by_taxpayer_id(taxpayer_public_id=taxpayer.public_id)

            for ta in taxpayer_attachments:
                if ta.attachment_id and ta.attachment_id not in seen_attachment_ids:
                    attachment = attachment_service.read_by_id(id=ta.attachment_id)
                    if attachment:
                        seen_attachment_ids.add(ta.attachment_id)
                        attachments_data.append({
                            "taxpayer_attachment": ta.to_dict(),
                            "attachment": attachment.to_dict()
                        })

        contacts = ContactService().read_by_vendor_id(vendor_id=vendor.id)
        return templates.TemplateResponse(
            "vendor/edit.html",
            {
                "request": request,
                "vendor": vendor_dict,
                "taxpayer": taxpayer.to_dict() if taxpayer else None,
                "vendor_type": vendor_type.to_dict() if vendor_type else None,
                "vendor_types": vendor_types,
                "address_types": address_types,
                "addresses_by_type": addresses_by_type,
                "vendor_addresses_by_type": vendor_addresses_by_type,
                "attachments": attachments_data,
                "contacts": [c.to_dict() for c in contacts],
                "parent_entity": "vendor",
                "parent_id": vendor.id,
                "current_user": current_user,
                "current_path": request.url.path,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
