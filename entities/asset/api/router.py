# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from entities.asset.api.schemas import (
    AssetAccountExclusionCreate,
    AssetCreate,
    AssetFinancingNoteCreate,
    AssetUpdate,
)
from entities.asset.business.service import (
    AssetAccountExclusionService,
    AssetFinancingNoteService,
    AssetService,
)
from core.workflow.api.process_engine import Channel, EventType, ProcessEngine, TriggerContext
from shared.api.responses import item_response, list_response, raise_not_found, raise_workflow_error
from shared.rbac import require_module_api
from shared.rbac_constants import Modules

router = APIRouter(prefix="/api/v1", tags=["api", "asset"])
service = AssetService()
financing_service = AssetFinancingNoteService()
exclusion_service = AssetAccountExclusionService()


@router.post("/create/asset")
def create_asset_router(
    body: AssetCreate,
    current_user: dict = Depends(require_module_api(Modules.ASSETS, "can_create")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload=body.model_dump(),
        workflow_type="asset_create",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create asset")
    return item_response(result.get("data"))


@router.get("/get/assets")
def get_assets_router(
    current_user: dict = Depends(require_module_api(Modules.ASSETS)),
):
    assets = service.read_all()
    return list_response([a.to_dict() for a in assets])


@router.get("/get/asset/{public_id}")
def get_asset_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.ASSETS)),
):
    data = service.read_with_qbo_by_public_id(public_id=public_id)
    if not data:
        raise_not_found("Asset")
    return item_response(data)


@router.put("/update/asset/{public_id}")
def update_asset_by_public_id_router(
    public_id: str,
    body: AssetUpdate,
    current_user: dict = Depends(require_module_api(Modules.ASSETS, "can_update")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={"public_id": public_id, **body.model_dump()},
        workflow_type="asset_update",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update asset")
    return item_response(result.get("data"))


@router.delete("/delete/asset/{public_id}")
def delete_asset_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.ASSETS, "can_delete")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={"public_id": public_id},
        workflow_type="asset_delete",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete asset")
    return item_response(result.get("data"))


@router.get("/get/assets/divergence-check")
def get_asset_divergence_check_router(
    current_user: dict = Depends(require_module_api(Modules.ASSETS)),
):
    return item_response(service.read_divergence_check())


@router.post("/create/asset-financing-note")
def create_asset_financing_note_router(
    body: AssetFinancingNoteCreate,
    current_user: dict = Depends(require_module_api(Modules.ASSETS, "can_create")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload=body.model_dump(),
        workflow_type="asset_financing_note_create",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create asset financing note")
    return item_response(result.get("data"))


@router.get("/get/asset-financing-notes/{asset_public_id}")
def get_asset_financing_notes_router(
    asset_public_id: str,
    current_user: dict = Depends(require_module_api(Modules.ASSETS)),
):
    notes = financing_service.read_by_asset_public_id(asset_public_id)
    return list_response([n.to_dict() for n in notes])


@router.delete("/delete/asset-financing-note/{public_id}")
def delete_asset_financing_note_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.ASSETS, "can_delete")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={"public_id": public_id},
        workflow_type="asset_financing_note_delete",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete asset financing note")
    return item_response(result.get("data"))


@router.post("/create/asset-account-exclusion")
def create_asset_account_exclusion_router(
    body: AssetAccountExclusionCreate,
    current_user: dict = Depends(require_module_api(Modules.ASSETS, "can_create")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload=body.model_dump(),
        workflow_type="asset_account_exclusion_create",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create asset account exclusion")
    return item_response(result.get("data"))


@router.get("/get/asset-account-exclusions")
def get_asset_account_exclusions_router(
    current_user: dict = Depends(require_module_api(Modules.ASSETS)),
):
    rows = exclusion_service.read_all()
    return list_response([r.to_dict() for r in rows])


@router.delete("/delete/asset-account-exclusion/{public_id}")
def delete_asset_account_exclusion_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.ASSETS, "can_delete")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={"public_id": public_id},
        workflow_type="asset_account_exclusion_delete",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete asset account exclusion")
    return item_response(result.get("data"))
