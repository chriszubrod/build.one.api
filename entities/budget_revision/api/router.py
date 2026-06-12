# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from entities.budget_revision.api.schemas import (
    BudgetRevisionApprove,
    BudgetRevisionCreate,
    BudgetRevisionUpdate,
)
from entities.budget_revision.business.service import BudgetRevisionService
from shared.api.responses import list_response, item_response, raise_workflow_error, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel


router = APIRouter(prefix="/api/v1", tags=["api", "budget_revision"])
service = BudgetRevisionService()


@router.post("/create/budget-revision")
def create_budget_revision_router(
    body: BudgetRevisionCreate,
    current_user: dict = Depends(require_module_api(Modules.BUDGETS, "can_create")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "budget_public_id": body.budget_public_id,
            "type": body.type or "change_order",
            "title": body.title,
            "description": body.description,
            "effective_date": body.effective_date,
        },
        workflow_type="budget_revision_create",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create budget revision")
    return item_response(result.get("data"))


# Specific routes BEFORE the parameterized /get/budget-revision/{public_id}.
@router.get("/get/budget-revisions/by-budget/{budget_public_id}")
def get_budget_revisions_by_budget_router(
    budget_public_id: str,
    current_user: dict = Depends(require_module_api(Modules.BUDGETS, "can_read")),
):
    try:
        items = service.read_by_budget_public_id(budget_public_id)
    except ValueError:
        raise_not_found("Budget")
    return list_response([i.to_dict() for i in items])


@router.get("/get/budget-revision/{public_id}")
def get_budget_revision_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.BUDGETS, "can_read")),
):
    item = service.read_by_public_id(public_id=public_id)
    if not item:
        raise_not_found("BudgetRevision")
    return item_response(item.to_dict())


@router.put("/update/budget-revision/{public_id}")
def update_budget_revision_router(
    public_id: str,
    body: BudgetRevisionUpdate,
    current_user: dict = Depends(require_module_api(Modules.BUDGETS, "can_update")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "row_version": body.row_version,
            "title": body.title,
            "description": body.description,
            "effective_date": body.effective_date,
        },
        workflow_type="budget_revision_update",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update budget revision")
    if result.get("data") is None:
        raise_not_found("BudgetRevision")
    return item_response(result.get("data"))


@router.delete("/delete/budget-revision/{public_id}")
def delete_budget_revision_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.BUDGETS, "can_delete")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={"public_id": public_id},
        workflow_type="budget_revision_delete",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete budget revision")
    if result.get("data") is None:
        raise_not_found("BudgetRevision")
    return item_response(result.get("data"))


# ============================================
# Status Transition Routes (direct service call — TimeEntry precedent)
# ============================================

@router.post("/approve/budget-revision/{public_id}")
def approve_budget_revision_router(
    public_id: str,
    body: BudgetRevisionApprove,
    current_user: dict = Depends(require_module_api(Modules.BUDGETS, "can_approve")),
):
    """Approve a draft change order. row_version is required in the body —
    a body-less approve has a stale-read race. Originals (Rev 0) are approved
    via budget activation instead."""
    try:
        approved = service.approve_by_public_id(
            public_id=public_id,
            row_version=body.row_version,
        )
        if approved is None:
            raise_not_found("BudgetRevision")
        return item_response(approved.to_dict())
    except ValueError as e:
        raise_workflow_error(str(e), "Failed to approve budget revision")
