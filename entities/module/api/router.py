# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.module.api.schemas import ModuleCreate, ModuleUpdate
from entities.module.business.service import ModuleService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from workflows.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import list_response, item_response, raise_workflow_error

router = APIRouter(prefix="/api/v1", tags=["api", "module"])


@router.post("/create/module")
def create_module_router(body: ModuleCreate, current_user: dict = Depends(require_module_api(Modules.ROLES, "can_create"))):
    """
    Create a new module.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "name": body.name,
            "route": body.route,
        },
        workflow_type="module_create",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create module")
    
    return item_response(result.get("data"))


@router.get("/get/modules")
def get_modules_router(current_user: dict = Depends(require_module_api(Modules.ROLES))):
    """
    Read all modules.
    """
    modules = ModuleService().read_all()
    return list_response([module.to_dict() for module in modules])


@router.get("/get/module/{public_id}")
def get_module_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.ROLES))):
    """
    Read a module by public ID.
    """
    module = ModuleService().read_by_public_id(public_id=public_id)
    return item_response(module.to_dict())


@router.put("/update/module/{public_id}")
def update_module_by_public_id_router(public_id: str, body: ModuleUpdate, current_user: dict = Depends(require_module_api(Modules.ROLES, "can_update"))):
    """
    Update a module by public ID.
    
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
            "route": body.route,
        },
        workflow_type="module_update",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update module")
    
    return item_response(result.get("data"))


@router.delete("/delete/module/{public_id}")
def delete_module_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.ROLES, "can_delete"))):
    """
    Delete a module by public ID.
    
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
        workflow_type="module_delete",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete module")
    
    return item_response(result.get("data"))
