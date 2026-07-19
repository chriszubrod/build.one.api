# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException

# Local Imports
from entities.vendor_insurance_policy.api.schemas import (
    VendorInsurancePolicyCreate,
    VendorInsurancePolicyUpdate,
)
from entities.vendor_insurance_policy.business.service import VendorInsurancePolicyService
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import list_response, item_response, raise_workflow_error, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

router = APIRouter(prefix="/api/v1", tags=["api", "vendor-insurance-policy"])
service = VendorInsurancePolicyService()


@router.post("/create/vendor-insurance-policy")
def create_vendor_insurance_policy_router(
    body: VendorInsurancePolicyCreate,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_create")),
):
    """
    Create a new vendor insurance policy.

    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "compliance_document_public_id": body.compliance_document_public_id,
            "coverage_type": body.coverage_type,
            "carrier": body.carrier,
            "policy_number": body.policy_number,
            "each_occurrence": body.each_occurrence,
            "aggregate": body.aggregate,
            "effective_date": body.effective_date,
            "expiry_date": body.expiry_date,
        },
        workflow_type="vendor_insurance_policy_create",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create vendor insurance policy")

    return item_response(result.get("data"))


@router.get("/get/vendor-insurance-policies/by-document/{compliance_document_public_id}")
def get_vendor_insurance_policies_by_document_router(
    compliance_document_public_id: str,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_read")),
):
    """
    Read vendor insurance policies by compliance document public ID.
    """
    try:
        policies = service.read_by_compliance_document_public_id(doc_public_id=compliance_document_public_id)
        return list_response([policy.to_dict() for policy in policies])
    except ValueError:
        raise_not_found("Compliance document")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/vendor-insurance-policy/{public_id}")
def get_vendor_insurance_policy_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_read")),
):
    """
    Read a vendor insurance policy by public ID.
    """
    try:
        policy = service.read_by_public_id(public_id=public_id)
        if not policy:
            raise_not_found("Vendor insurance policy")
        return item_response(policy.to_dict())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/update/vendor-insurance-policy/{public_id}")
def update_vendor_insurance_policy_by_public_id_router(
    public_id: str,
    body: VendorInsurancePolicyUpdate,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_update")),
):
    """
    Update a vendor insurance policy by public ID.

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
            "coverage_type": body.coverage_type,
            "carrier": body.carrier,
            "policy_number": body.policy_number,
            "each_occurrence": body.each_occurrence,
            "aggregate": body.aggregate,
            "effective_date": body.effective_date,
            "expiry_date": body.expiry_date,
        },
        workflow_type="vendor_insurance_policy_update",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update vendor insurance policy")

    return item_response(result.get("data"))


@router.delete("/delete/vendor-insurance-policy/{public_id}")
def delete_vendor_insurance_policy_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_delete")),
):
    """
    Delete a vendor insurance policy by public ID.

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
        workflow_type="vendor_insurance_policy_delete",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete vendor insurance policy")

    return item_response(result.get("data"))
