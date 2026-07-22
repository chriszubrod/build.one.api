# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException

# Local Imports
from entities.business_license_attachment.api.schemas import BusinessLicenseAttachmentCreate
from entities.business_license_attachment.business.service import BusinessLicenseAttachmentService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import list_response, item_response, raise_workflow_error, raise_not_found

router = APIRouter(prefix="/api/v1", tags=["api", "business_license_attachment"])
service = BusinessLicenseAttachmentService()


@router.post("/create/business-license-attachment")
def create_business_license_attachment_router(
    body: BusinessLicenseAttachmentCreate,
    current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS, "can_create")),
):
    """
    Create a new business license attachment.

    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "business_license_public_id": body.business_license_public_id,
            "attachment_public_id": body.attachment_public_id,
        },
        workflow_type="business_license_attachment_create",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create business license attachment")

    return item_response(result.get("data"))


@router.get("/get/business-license-attachments")
def get_business_license_attachments_router(
    current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS)),
):
    """
    Read all business license attachments.
    """
    try:
        attachments = service.read_all()
        return list_response([a.to_dict() for a in attachments])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/business-license-attachment/{public_id}")
def get_business_license_attachment_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS)),
):
    """
    Read a business license attachment by public ID.
    """
    try:
        attachment = service.read_by_public_id(public_id=public_id)
        if not attachment:
            raise_not_found("Business license attachment")
        return item_response(attachment.to_dict())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/business-license-attachments/{business_license_id}")
def get_business_license_attachments_by_business_license_id_router(
    business_license_id: str,
    current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS)),
):
    """
    Read business license attachments by business license public ID.
    """
    try:
        attachments = service.read_by_business_license_id(
            business_license_public_id=business_license_id
        )
        return list_response([a.to_dict() for a in attachments])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete/business-license-attachment/{public_id}")
def delete_business_license_attachment_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.ATTACHMENTS, "can_delete")),
):
    """
    Delete a business license attachment by public ID.

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
        workflow_type="business_license_attachment_delete",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete business license attachment")

    return item_response(result.get("data"))
