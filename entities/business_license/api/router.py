# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from entities.business_license.api.schemas import BusinessLicenseCreate, BusinessLicenseUpdate
from entities.business_license.business.service import BusinessLicenseService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import list_response, item_response, raise_workflow_error

router = APIRouter(prefix="/api/v1", tags=["api", "business_license"])
service = BusinessLicenseService()


@router.post("/create/business-license")
def create_business_license_router(
    body: BusinessLicenseCreate,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_create")),
):
    """
    Create a new business license.

    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "vendor_public_id": body.vendor_public_id,
            "license_number": body.license_number,
            "issuing_authority": body.issuing_authority,
            "issue_date": body.issue_date,
            "expiry_date": body.expiry_date,
            "verification_status": body.verification_status,
        },
        workflow_type="business_license_create",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create business license")

    return item_response(result.get("data"))


@router.get("/get/business-licenses")
def get_business_licenses_router(current_user: dict = Depends(require_module_api(Modules.VENDORS))):
    """
    Read all business licenses.
    """
    licenses = service.read_all()
    return list_response([lic.to_dict() for lic in licenses])


@router.get("/get/business-license/{public_id}")
def get_business_license_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.VENDORS)),
):
    """
    Read a business license by public ID.
    """
    lic = service.read_by_public_id(public_id=public_id)
    return item_response(lic.to_dict())


@router.get("/get/business-licenses/by-vendor/{vendor_public_id}")
def get_business_licenses_by_vendor_public_id_router(
    vendor_public_id: str,
    current_user: dict = Depends(require_module_api(Modules.VENDORS)),
):
    """
    Read business licenses by vendor public ID.
    """
    licenses = service.read_by_vendor_public_id(vendor_public_id=vendor_public_id)
    return list_response([lic.to_dict() for lic in licenses])


@router.put("/update/business-license/{public_id}")
def update_business_license_by_public_id_router(
    public_id: str,
    body: BusinessLicenseUpdate,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_update")),
):
    """
    Update a business license by public ID.

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
            "license_number": body.license_number,
            "issuing_authority": body.issuing_authority,
            "issue_date": body.issue_date,
            "expiry_date": body.expiry_date,
            "verification_status": body.verification_status,
        },
        workflow_type="business_license_update",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update business license")

    return item_response(result.get("data"))


@router.delete("/delete/business-license/{public_id}")
def delete_business_license_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_delete")),
):
    """
    Delete a business license by public ID.

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
        workflow_type="business_license_delete",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete business license")

    return item_response(result.get("data"))
