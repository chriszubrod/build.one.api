# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.vendor_address.api.schemas import VendorAddressCreate, VendorAddressUpdate
from entities.vendor_address.business.service import VendorAddressService
from entities.auth.business.service import get_current_user_api
from workflows.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel

router = APIRouter(prefix="/api/v1", tags=["api", "vendor_address"])


@router.post("/create/vendor_address")
def create_vendor_address_router(body: VendorAddressCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new vendor address.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "vendor_id": body.vendor_id,
            "address_id": body.address_id,
            "address_type_id": body.address_type_id,
        },
        workflow_type="vendor_address_create",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create vendor address")
        )
    
    return result.get("data")


@router.get("/get/vendor_addresses")
def get_vendor_addresses_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all vendor addresses.
    """
    vendor_addresses = VendorAddressService().read_all()
    return [vendor_address.to_dict() for vendor_address in vendor_addresses]


@router.get("/get/vendor_address/{public_id}")
def get_vendor_address_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a vendor address by public ID.
    """
    vendor_address = VendorAddressService().read_by_public_id(public_id=public_id)
    return vendor_address.to_dict()


@router.get("/get/vendor_address/vendor/{vendor_id}")
def get_vendor_address_by_vendor_id_router(vendor_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a vendor address by vendor ID.
    """
    vendor_address = VendorAddressService().read_by_vendor_id(vendor_id=vendor_id)
    return vendor_address.to_dict()


@router.get("/get/vendor_address/address/{address_id}")
def get_vendor_address_by_address_id_router(address_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a vendor address by address ID.
    """
    vendor_address = VendorAddressService().read_by_address_id(address_id=address_id)
    return vendor_address.to_dict()


@router.get("/get/vendor_address/address_type/{address_type_id}")
def get_vendor_address_by_address_type_id_router(address_type_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a vendor address by address type ID.
    """
    vendor_address = VendorAddressService().read_by_address_type_id(address_type_id=address_type_id)
    return vendor_address.to_dict()


@router.put("/update/vendor_address/{public_id}")
def update_vendor_address_by_public_id_router(public_id: str, body: VendorAddressUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update a vendor address by public ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "row_version": body.row_version,
            "vendor_id": body.vendor_id,
            "address_id": body.address_id,
            "address_type_id": body.address_type_id,
        },
        workflow_type="vendor_address_update",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update vendor address")
        )
    
    return result.get("data")


@router.delete("/delete/vendor_address/{public_id}")
def delete_vendor_address_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a vendor address by public ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
        },
        workflow_type="vendor_address_delete",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete vendor address")
        )
    
    return result.get("data")
