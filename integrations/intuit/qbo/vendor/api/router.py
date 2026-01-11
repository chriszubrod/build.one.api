# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from integrations.intuit.qbo.vendor.api.schemas import QboVendorCreate, QboVendorUpdate, QboVendorSync
from integrations.intuit.qbo.vendor.business.service import QboVendorService
from modules.auth.business.service import get_current_user_api as get_current_qbo_vendor_api

router = APIRouter(prefix="/api/v1", tags=["api", "qbo-vendor"])
service = QboVendorService()


@router.post("/sync/qbo-vendors")
def sync_qbo_vendors_router(body: QboVendorSync, current_user: dict = Depends(get_current_qbo_vendor_api)):
    """
    Sync Vendors from QBO.
    """
    vendors = service.sync_from_qbo(
        realm_id=body.realm_id,
        last_updated_time=body.last_updated_time,
        sync_to_modules=body.sync_to_modules
    )
    return [vendor.to_dict() for vendor in vendors]


@router.get("/get/qbo-vendors/realm/{realm_id}")
def get_qbo_vendors_by_realm_id_router(realm_id: str, current_user: dict = Depends(get_current_qbo_vendor_api)):
    """
    Read all QBO vendors by realm ID.
    """
    vendors = service.read_by_realm_id(realm_id=realm_id)
    return [vendor.to_dict() for vendor in vendors]


@router.get("/get/qbo-vendor/qbo-id/{qbo_id}")
def get_qbo_vendor_by_qbo_id_router(qbo_id: str, current_user: dict = Depends(get_current_qbo_vendor_api)):
    """
    Read a QBO vendor by QBO ID.
    """
    vendor = service.read_by_qbo_id(qbo_id=qbo_id)
    return vendor.to_dict() if vendor else None


@router.post("/create/qbo-vendor")
def create_qbo_vendor_router(body: QboVendorCreate, current_user: dict = Depends(get_current_qbo_vendor_api)):
    """
    Create a new QBO vendor.
    """
    vendor = service.create(
        id=body.id,
        sync_token=body.sync_token,
        display_name=body.display_name,
        vendor_1099=body.vendor_1099,
        company_name=body.company_name,
        tax_identifier=body.tax_identifier,
        print_on_check_name=body.print_on_check_name,
        bill_addr_id=body.bill_addr_id,
    )
    return vendor.to_dict()


@router.get("/get/qbo-vendors")
def get_qbo_vendors_router(current_user: dict = Depends(get_current_qbo_vendor_api)):
    """
    Read all QBO vendors.
    """
    vendors = service.read_all()
    return [vendor.to_dict() for vendor in vendors]


@router.get("/get/qbo-vendor/{id}")
def get_qbo_vendor_by_id_router(id: str, current_user: dict = Depends(get_current_qbo_vendor_api)):
    """
    Read a QBO vendor by ID.
    """
    vendor = service.read_by_id(id=id)
    return vendor.to_dict()


@router.get("/get/qbo-vendor/sync-token/{sync_token}")
def get_qbo_vendor_by_sync_token_router(sync_token: str, current_user: dict = Depends(get_current_qbo_vendor_api)):
    """
    Read a QBO vendor by sync token.
    """
    vendor = service.read_by_sync_token(sync_token=sync_token)
    return vendor.to_dict()


@router.get("/get/qbo-vendor/display-name/{display_name}")
def get_qbo_vendor_by_display_name_router(display_name: str, current_user: dict = Depends(get_current_qbo_vendor_api)):
    """
    Read a QBO vendor by display name.
    """
    vendor = service.read_by_display_name(display_name=display_name)
    return vendor.to_dict()


@router.get("/get/qbo-vendor/company-name/{company_name}")
def get_qbo_vendor_by_company_name_router(company_name: str, current_user: dict = Depends(get_current_qbo_vendor_api)):
    """
    Read a QBO vendor by company name.
    """
    vendor = service.read_by_company_name(company_name=company_name)
    return vendor.to_dict()


@router.get("/get/qbo-vendor/tax-identifier/{tax_identifier}")
def get_qbo_vendor_by_tax_identifier_router(tax_identifier: str, current_user: dict = Depends(get_current_qbo_vendor_api)):
    """
    Read a QBO vendor by tax identifier.
    """
    vendor = service.read_by_tax_identifier(tax_identifier=tax_identifier)
    return vendor.to_dict()


@router.put("/update/qbo-vendor/{id}")
def update_qbo_vendor_by_id_router(id: str, body: QboVendorUpdate, current_user: dict = Depends(get_current_qbo_vendor_api)):
    """
    Update a QBO vendor by ID.
    """
    vendor = service.update_by_id(id=id, sync_token=body.sync_token, display_name=body.display_name, vendor_1099=body.vendor_1099, company_name=body.company_name, tax_identifier=body.tax_identifier, print_on_check_name=body.print_on_check_name, bill_addr_id=body.bill_addr_id)
    return vendor.to_dict()


@router.delete("/delete/qbo-vendor/{id}")
def delete_qbo_vendor_by_id_router(id: str, current_user: dict = Depends(get_current_qbo_vendor_api)):
    """
    Delete a QBO vendor by ID.
    """
    vendor = service.delete_by_id(id=id)
    return vendor.to_dict()
