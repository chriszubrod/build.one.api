# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.vendor.api.schemas import VendorCreate, VendorUpdate
from entities.vendor.business.service import VendorService
from shared.api.responses import list_response, item_response, raise_workflow_error, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from workflows.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel

router = APIRouter(prefix="/api/v1", tags=["api", "vendor"])
service = VendorService()


@router.post("/create/vendor")
def create_vendor_router(body: VendorCreate, current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_create"))):
    """
    Create a new vendor.

    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "name": body.name,
            "abbreviation": body.abbreviation,
            "taxpayer_public_id": body.taxpayer_public_id,
            "vendor_type_public_id": body.vendor_type_public_id,
            "is_draft": body.is_draft,
            "is_contract_labor": body.is_contract_labor,
        },
        workflow_type="vendor_create",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create vendor")

    return item_response(result.get("data"))


@router.get("/get/vendors")
def get_vendors_router(current_user: dict = Depends(require_module_api(Modules.VENDORS))):
    """
    Read all vendors.
    """
    vendors = service.read_all()
    return list_response([vendor.to_dict() for vendor in vendors])


@router.get("/get/vendor/{public_id}")
def get_vendor_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.VENDORS))):
    """
    Read a vendor by public ID.
    """
    vendor = service.read_by_public_id(public_id=public_id)
    if not vendor:
        raise_not_found("Vendor")
    return item_response(vendor.to_dict())


@router.put("/update/vendor/{public_id}")
def update_vendor_by_public_id_router(public_id: str, body: VendorUpdate, current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_update"))):
    """
    Update a vendor by public ID.

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
            "name": body.name,
            "abbreviation": body.abbreviation,
            "taxpayer_public_id": body.taxpayer_public_id,
            "vendor_type_public_id": body.vendor_type_public_id,
            "is_draft": body.is_draft,
            "is_contract_labor": body.is_contract_labor,
        },
        workflow_type="vendor_update",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update vendor")

    return item_response(result.get("data"))


@router.delete("/delete/vendor/{public_id}")
def delete_vendor_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_delete"))):
    """
    Soft delete a vendor by public ID.

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
        workflow_type="vendor_delete",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete vendor")

    return item_response(result.get("data"))
