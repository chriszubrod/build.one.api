# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, HTTPException, Depends, status

# Local Imports
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.cost_code.business.service import CostCodeService
from entities.sub_cost_code.api.schemas import (
    SubCostCodeCreate,
    SubCostCodeUpdate,
)
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel
from shared.api.responses import list_response, item_response, raise_workflow_error, raise_not_found

router = APIRouter(prefix="/api/v1", tags=["api", "sub-cost-code"])
service = SubCostCodeService()
cost_code_service = CostCodeService()


def _resolve_cost_code_id(cost_code_public_id: str) -> int:
    """Resolve a cost code public_id to its internal id."""
    cost_code = cost_code_service.read_by_public_id(cost_code_public_id)
    if not cost_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cost code not found: {cost_code_public_id}",
        )
    return cost_code.id


@router.post("/create/sub-cost-code")
def create_sub_cost_code_router(body: SubCostCodeCreate, current_user: dict = Depends(require_module_api(Modules.COST_CODES, "can_create"))):
    """
    Create a new sub cost code.

    Routes through the workflow engine for audit logging and state tracking.
    """
    cost_code_id = _resolve_cost_code_id(body.cost_code_public_id)

    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "number": body.number,
            "name": body.name,
            "description": body.description,
            "cost_code_id": cost_code_id,
            "aliases": body.aliases,
        },
        workflow_type="sub_cost_code_create",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create sub cost code")

    return item_response(result.get("data"))


@router.get("/get/sub-cost-codes")
def get_sub_cost_codes_router(current_user: dict = Depends(require_module_api(Modules.COST_CODES))):
    """
    Read all sub cost codes.
    """
    sub_cost_codes = service.read_all()
    if not sub_cost_codes:
        raise_not_found("Sub cost codes.")
    return list_response([sub_cost_code.to_dict() for sub_cost_code in sub_cost_codes])


@router.get("/get/sub-cost-code/{public_id}")
def get_sub_cost_code_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.COST_CODES))):
    """
    Read a sub cost code by public ID.
    """
    sub_cost_code = service.read_by_public_id(public_id=public_id)
    if not sub_cost_code:
        raise_not_found("Sub cost code.")
    return item_response(sub_cost_code.to_dict())


@router.put("/update/sub-cost-code/{public_id}")
def update_sub_cost_code_by_id_router(public_id: str, body: SubCostCodeUpdate, current_user: dict = Depends(require_module_api(Modules.COST_CODES, "can_update"))):
    """
    Update a sub cost code by ID.

    Routes through the workflow engine for audit logging and state tracking.
    """
    cost_code_id = _resolve_cost_code_id(body.cost_code_public_id)

    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "row_version": body.row_version,
            "number": body.number,
            "name": body.name,
            "description": body.description,
            "cost_code_id": cost_code_id,
            "aliases": body.aliases,
        },
        workflow_type="sub_cost_code_update",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update sub cost code")

    return item_response(result.get("data"))


@router.delete("/delete/sub-cost-code/{public_id}")
def delete_sub_cost_code_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.COST_CODES, "can_delete"))):
    """
    Soft delete a sub cost code by ID.

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
        workflow_type="sub_cost_code_delete",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete sub cost code")

    return item_response(result.get("data"))
