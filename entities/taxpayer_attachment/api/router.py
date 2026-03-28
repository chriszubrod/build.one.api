# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.taxpayer_attachment.api.schemas import TaxpayerAttachmentCreate
from entities.taxpayer_attachment.business.service import TaxpayerAttachmentService
from entities.auth.business.service import get_current_user_api as get_current_taxpayer_attachment_api
from workflows.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel

router = APIRouter(prefix="/api/v1", tags=["api", "taxpayer_attachment"])
service = TaxpayerAttachmentService()


@router.post("/create/taxpayer-attachment")
def create_taxpayer_attachment_router(
    body: TaxpayerAttachmentCreate, current_user: dict = Depends(get_current_taxpayer_attachment_api)
):
    """
    Create a new taxpayer attachment.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "taxpayer_public_id": body.taxpayer_public_id,
            "attachment_public_id": body.attachment_public_id,
        },
        workflow_type="taxpayer_attachment_create",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create taxpayer attachment")
        )
    
    return result.get("data")


@router.get("/get/taxpayer-attachments")
def get_taxpayer_attachments_router(current_user: dict = Depends(get_current_taxpayer_attachment_api)):
    """
    Read all taxpayer attachments.
    """
    try:
        taxpayer_attachments = service.read_all()
        return [ta.to_dict() for ta in taxpayer_attachments]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/taxpayer-attachment/{public_id}")
def get_taxpayer_attachment_by_public_id_router(
    public_id: str, current_user: dict = Depends(get_current_taxpayer_attachment_api)
):
    """
    Read a taxpayer attachment by public ID.
    """
    try:
        taxpayer_attachment = service.read_by_public_id(public_id=public_id)
        if not taxpayer_attachment:
            raise HTTPException(status_code=404, detail="Taxpayer attachment not found")
        return taxpayer_attachment.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/get/taxpayer-attachments/{taxpayer_id}")
def get_taxpayer_attachments_by_taxpayer_id_router(
    taxpayer_id: str, current_user: dict = Depends(get_current_taxpayer_attachment_api)
):
    """
    Read taxpayer attachments by taxpayer public ID.
    """
    try:
        taxpayer_attachments = service.read_by_taxpayer_id(taxpayer_public_id=taxpayer_id)
        return [ta.to_dict() for ta in taxpayer_attachments]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete/taxpayer-attachment/{public_id}")
def delete_taxpayer_attachment_by_public_id_router(
    public_id: str, current_user: dict = Depends(get_current_taxpayer_attachment_api)
):
    """
    Delete a taxpayer attachment by public ID.
    
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
        workflow_type="taxpayer_attachment_delete",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete taxpayer attachment")
        )
    
    return result.get("data")

