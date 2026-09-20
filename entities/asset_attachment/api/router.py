# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from entities.asset_attachment.api.schemas import AssetAttachmentCreate
from entities.asset_attachment.business.service import AssetAttachmentService
from core.workflow.api.process_engine import Channel, EventType, ProcessEngine, TriggerContext
from shared.api.responses import item_response, list_response, raise_not_found, raise_workflow_error
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

router = APIRouter(prefix="/api/v1", tags=["api", "asset_attachment"])
service = AssetAttachmentService()


@router.post("/create/asset-attachment")
def create_asset_attachment_router(
    body: AssetAttachmentCreate,
    current_user: dict = Depends(require_module_api(Modules.ASSETS, "can_create")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload=body.model_dump(),
        workflow_type="asset_attachment_create",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create asset attachment")
    return item_response(result.get("data"))


@router.get("/get/asset-attachments/{asset_public_id}")
def get_asset_attachments_router(
    asset_public_id: str,
    current_user: dict = Depends(require_module_api(Modules.ASSETS)),
):
    attachments = service.read_by_asset_public_id(asset_public_id)
    return list_response([a.to_dict() for a in attachments])


@router.delete("/delete/asset-attachment/{public_id}")
def delete_asset_attachment_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.ASSETS, "can_delete")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={"public_id": public_id},
        workflow_type="asset_attachment_delete",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete asset attachment")
    return item_response(result.get("data"))
