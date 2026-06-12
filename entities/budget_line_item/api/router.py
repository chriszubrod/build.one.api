# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from entities.budget_line_item.api.schemas import BudgetLineItemCreate, BudgetLineItemUpdate
from entities.budget_line_item.business.service import BudgetLineItemService
from shared.api.responses import list_response, item_response, raise_workflow_error, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel


router = APIRouter(prefix="/api/v1", tags=["api", "budget_line_item"])
service = BudgetLineItemService()


def _dec(value):
    """Decimals transport as strings through the workflow payload — never float."""
    return str(value) if value is not None else None


@router.post("/create/budget-line-item")
def create_budget_line_item_router(
    body: BudgetLineItemCreate,
    current_user: dict = Depends(require_module_api(Modules.BUDGETS, "can_create")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "budget_revision_public_id": body.budget_revision_public_id,
            "sub_cost_code_id": body.sub_cost_code_id,
            "description": body.description,
            "quantity": _dec(body.quantity),
            "rate": _dec(body.rate),
            "amount": _dec(body.amount),
            "markup": _dec(body.markup),
            "price": _dec(body.price),
        },
        workflow_type="budget_line_item_create",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create budget line item")
    return item_response(result.get("data"))


# Specific route declared before the parameterized /get/budget-line-item/{public_id}.
@router.get("/get/budget-line-items/by-revision/{revision_public_id}")
def get_budget_line_items_by_revision_router(
    revision_public_id: str,
    current_user: dict = Depends(require_module_api(Modules.BUDGETS, "can_read")),
):
    items = service.read_by_budget_revision_public_id(revision_public_id)
    if items is None:
        raise_not_found("BudgetRevision")
    return list_response([i.to_dict() for i in items])


@router.get("/get/budget-line-item/{public_id}")
def get_budget_line_item_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.BUDGETS, "can_read")),
):
    item = service.read_by_public_id(public_id=public_id)
    if not item:
        raise_not_found("BudgetLineItem")
    return item_response(item.to_dict())


@router.put("/update/budget-line-item/{public_id}")
def update_budget_line_item_router(
    public_id: str,
    body: BudgetLineItemUpdate,
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
            "sub_cost_code_id": body.sub_cost_code_id,
            "description": body.description,
            "quantity": _dec(body.quantity),
            "rate": _dec(body.rate),
            "amount": _dec(body.amount),
            "markup": _dec(body.markup),
            "price": _dec(body.price),
        },
        workflow_type="budget_line_item_update",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update budget line item")
    if result.get("data") is None:
        raise_not_found("BudgetLineItem")
    return item_response(result.get("data"))


@router.delete("/delete/budget-line-item/{public_id}")
def delete_budget_line_item_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.BUDGETS, "can_delete")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={"public_id": public_id},
        workflow_type="budget_line_item_delete",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete budget line item")
    if result.get("data") is None:
        raise_not_found("BudgetLineItem")
    return item_response(result.get("data"))
