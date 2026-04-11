# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.integration.api.schemas import IntegrationCreate, IntegrationUpdate
from entities.integration.business.service import IntegrationService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from workflows.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import list_response, item_response, raise_workflow_error

router = APIRouter(prefix="/api/v1", tags=["api", "integration"])


@router.post("/create/integration")
def create_integration_router(body: IntegrationCreate, current_user: dict = Depends(require_module_api(Modules.INTEGRATIONS, "can_create"))):
    """
    Create a new integration.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "name": body.name,
            "status": body.status,
        },
        workflow_type="integration_create",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create integration")
    
    return item_response(result.get("data"))


@router.get("/get/integrations")
def get_integrations_router(current_user: dict = Depends(require_module_api(Modules.INTEGRATIONS))):
    """
    Read all integrations.
    """
    integrations = IntegrationService().read_all()
    return list_response([integration.to_dict() for integration in integrations])


@router.get("/get/integration/{public_id}")
def get_integration_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.INTEGRATIONS))):
    """
    Read a integration by public ID.
    """
    integration = IntegrationService().read_by_public_id(public_id=public_id)
    return item_response(integration.to_dict())


@router.put("/update/integration/{public_id}")
def update_integration_by_public_id_router(public_id: str, body: IntegrationUpdate, current_user: dict = Depends(require_module_api(Modules.INTEGRATIONS, "can_update"))):
    """
    Update a integration by public ID.
    
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
            "status": body.status.value if body.status else None,
        },
        workflow_type="integration_update",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update integration")
    
    return item_response(result.get("data"))


@router.delete("/delete/integration/{public_id}")
def delete_integration_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.INTEGRATIONS, "can_delete"))):
    """
    Delete a integration by public ID.
    
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
        workflow_type="integration_delete",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete integration")
    
    return item_response(result.get("data"))


@router.get("/connect/integration/{public_id}")
def connect_integration_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.INTEGRATIONS))):
    """
    Connect an integration by routing to the appropriate integration handler.
    Returns a dict with redirect_url for OAuth flows or success/error message.
    """
    result = IntegrationService().connect(public_id=public_id)
    return result


@router.post("/disconnect/integration/{public_id}")
def disconnect_integration_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.INTEGRATIONS, "can_update"))):
    """
    Disconnect an integration by routing to the appropriate integration handler.
    Returns a dict with success/error message.
    """
    result = IntegrationService().disconnect(public_id=public_id)
    return result
