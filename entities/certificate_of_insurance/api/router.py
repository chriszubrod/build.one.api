# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from entities.certificate_of_insurance.api.schemas import (
    CertificateOfInsuranceCreate,
    CertificateOfInsuranceUpdate,
)
from entities.certificate_of_insurance.business.service import CertificateOfInsuranceService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import list_response, item_response, raise_workflow_error

router = APIRouter(prefix="/api/v1", tags=["api", "certificate_of_insurance"])
service = CertificateOfInsuranceService()


@router.post("/create/certificate-of-insurance")
def create_certificate_of_insurance_router(
    body: CertificateOfInsuranceCreate,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_create")),
):
    """
    Create a new certificate of insurance.

    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "vendor_public_id": body.vendor_public_id,
            "issuing_authority": body.issuing_authority,
            "issue_date": body.issue_date,
            "attachment_id": body.attachment_id,
            "verification_status": body.verification_status,
        },
        workflow_type="certificate_of_insurance_create",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create certificate of insurance")

    return item_response(result.get("data"))


@router.get("/get/certificates-of-insurance")
def get_certificates_of_insurance_router(current_user: dict = Depends(require_module_api(Modules.VENDORS))):
    """
    Read all certificates of insurance.
    """
    certs = service.read_all()
    return list_response([cert.to_dict() for cert in certs])


@router.get("/get/certificate-of-insurance/{public_id}")
def get_certificate_of_insurance_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.VENDORS)),
):
    """
    Read a certificate of insurance by public ID.
    """
    cert = service.read_by_public_id(public_id=public_id)
    return item_response(cert.to_dict())


@router.get("/get/certificates-of-insurance/by-vendor/{vendor_public_id}")
def get_certificates_of_insurance_by_vendor_public_id_router(
    vendor_public_id: str,
    current_user: dict = Depends(require_module_api(Modules.VENDORS)),
):
    """
    Read certificates of insurance by vendor public ID.
    """
    certs = service.read_by_vendor_public_id(vendor_public_id=vendor_public_id)
    return list_response([cert.to_dict() for cert in certs])


@router.put("/update/certificate-of-insurance/{public_id}")
def update_certificate_of_insurance_by_public_id_router(
    public_id: str,
    body: CertificateOfInsuranceUpdate,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_update")),
):
    """
    Update a certificate of insurance by public ID.

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
            "issuing_authority": body.issuing_authority,
            "issue_date": body.issue_date,
            "attachment_id": body.attachment_id,
            "verification_status": body.verification_status,
        },
        workflow_type="certificate_of_insurance_update",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update certificate of insurance")

    return item_response(result.get("data"))


@router.delete("/delete/certificate-of-insurance/{public_id}")
def delete_certificate_of_insurance_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_delete")),
):
    """
    Delete a certificate of insurance by public ID.

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
        workflow_type="certificate_of_insurance_delete",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete certificate of insurance")

    return item_response(result.get("data"))
