# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException

# Local Imports
from integrations.intuit.qbo.physical_address.api.schemas import (
    QboPhysicalAddressCreate,
    QboPhysicalAddressUpdate,
    QboPhysicalAddressSyncRequest,
)
from integrations.intuit.qbo.physical_address.business.service import QboPhysicalAddressService
from modules.auth.business.service import get_current_user_api as get_current_qbo_physical_address_api

router = APIRouter(prefix="/api/v1", tags=["api", "qbo-physical-address"])
service = QboPhysicalAddressService()


@router.post("/intuit/qbo/physical-address/create")
def create_qbo_physical_address_router(body: QboPhysicalAddressCreate, current_user: dict = Depends(get_current_qbo_physical_address_api)):
    """
    Create a new QBO physical address.
    """
    address = service.create(
        qbo_id=body.qbo_id,
        line1=body.line1,
        line2=body.line2,
        city=body.city,
        country=body.country,
        country_sub_division_code=body.country_sub_division_code,
        postal_code=body.postal_code,
    )
    return address.to_dict()


@router.get("/intuit/qbo/physical-address/list")
def list_qbo_physical_addresses_router(current_user: dict = Depends(get_current_qbo_physical_address_api)):
    """
    Read all QBO physical addresses.
    """
    addresses = service.read_all()
    return [address.to_dict() for address in addresses]


@router.get("/intuit/qbo/physical-address/read/{id}")
def read_qbo_physical_address_by_id_router(id: int, current_user: dict = Depends(get_current_qbo_physical_address_api)):
    """
    Read a QBO physical address by ID.
    """
    address = service.read_by_id(id=id)
    if not address:
        raise HTTPException(status_code=404, detail="Physical address not found")
    return address.to_dict()


@router.put("/intuit/qbo/physical-address/update/{id}")
def update_qbo_physical_address_by_id_router(id: int, body: QboPhysicalAddressUpdate, current_user: dict = Depends(get_current_qbo_physical_address_api)):
    """
    Update a QBO physical address by ID.
    """
    address = service.update_by_id(
        id=id,
        row_version=body.row_version,
        qbo_id=body.qbo_id,
        line1=body.line1,
        line2=body.line2,
        city=body.city,
        country=body.country,
        country_sub_division_code=body.country_sub_division_code,
        postal_code=body.postal_code,
    )
    if not address:
        raise HTTPException(status_code=404, detail="Physical address not found")
    return address.to_dict()


@router.delete("/intuit/qbo/physical-address/delete/{id}")
def delete_qbo_physical_address_by_id_router(id: int, current_user: dict = Depends(get_current_qbo_physical_address_api)):
    """
    Delete a QBO physical address by ID.
    """
    address = service.delete_by_id(id=id)
    if not address:
        raise HTTPException(status_code=404, detail="Physical address not found")
    return address.to_dict()


@router.post("/intuit/qbo/physical-address/sync")
def sync_from_qbo_physical_address_router(body: QboPhysicalAddressSyncRequest, current_user: dict = Depends(get_current_qbo_physical_address_api)):
    """
    Sync a QBO physical address from QBO CompanyInfo API and store locally.
    """
    address = service.sync_from_qbo(
        access_token=body.access_token,
        realm_id=body.realm_id,
        qbo_id=body.qbo_id,
    )
    return address.to_dict()

