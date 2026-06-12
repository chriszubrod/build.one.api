# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from entities.budget.api.schemas import BudgetActivate, BudgetCreate, BudgetUpdate
from entities.budget.business.service import BudgetService
from shared.api.responses import list_response, item_response, raise_workflow_error, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel


router = APIRouter(prefix="/api/v1", tags=["api", "budget"])
service = BudgetService()


@router.post("/create/budget")
def create_budget_router(
    body: BudgetCreate,
    current_user: dict = Depends(require_module_api(Modules.BUDGETS, "can_create")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "project_public_id": body.project_public_id,
            "notes": body.notes,
        },
        workflow_type="budget_create",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create budget")
    return item_response(result.get("data"))


@router.get("/get/budgets")
def get_budgets_router(
    current_user: dict = Depends(require_module_api(Modules.BUDGETS, "can_read")),
):
    # Rows carry contract_value / drawn_price / remaining_to_draw —
    # server-computed (the web layer must never do currency math).
    return list_response(service.read_all_with_rollups())


# NOTE: path shapes differ (4 segments vs 3) so FastAPI can't confuse these
# with /get/budget/{public_id} regardless of order — declaration order only
# matters for SAME-shape overlapping templates. Kept grouped for readability.
@router.get("/get/budget/by-project/{project_public_id}")
def get_budget_by_project_router(
    project_public_id: str,
    current_user: dict = Depends(require_module_api(Modules.BUDGETS, "can_read")),
):
    try:
        item = service.read_by_project_public_id(project_public_id=project_public_id)
    except ValueError:
        # The only ValueError here is an unknown project public_id.
        raise_not_found("Project")
    if not item:
        raise_not_found("Budget")
    return item_response(item.to_dict())


# Variance: SCC-grain rows + CostCode subtotals + grand totals, all
# server-computed Decimal (strings on the wire).
# ACCESS DECISION (2026-06-12): gated on Budgets can_read only — the payload
# aggregates Bills/Expenses/Credits/Labor/Invoices, so any role granted
# Budgets read (TA/Controller/PM/Reviewer/Auditor per migration 005) sees
# project cost aggregates without per-entity grants. Deliberate: those are
# exactly the cost-visibility roles; project scoping still applies.
@router.get("/get/budget/{public_id}/variance")
def get_budget_variance_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.BUDGETS, "can_read")),
):
    payload = service.variance_by_public_id(public_id=public_id)
    if payload is None:
        raise_not_found("Budget")
    return item_response(payload)


@router.get("/get/budget/{public_id}")
def get_budget_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.BUDGETS, "can_read")),
):
    item = service.read_by_public_id(public_id=public_id)
    if not item:
        raise_not_found("Budget")
    return item_response(item.to_dict())


@router.put("/update/budget/{public_id}")
def update_budget_router(
    public_id: str,
    body: BudgetUpdate,
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
            "notes": body.notes,
        },
        workflow_type="budget_update",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update budget")
    if result.get("data") is None:
        raise_not_found("Budget")
    return item_response(result.get("data"))


@router.delete("/delete/budget/{public_id}")
def delete_budget_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.BUDGETS, "can_delete")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={"public_id": public_id},
        workflow_type="budget_delete",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete budget")
    if result.get("data") is None:
        raise_not_found("Budget")
    return item_response(result.get("data"))


# Action endpoint — direct service call (TimeEntry approve precedent), no
# Workflow row for the transition (accepted knowingly per the plan).
@router.post("/activate/budget/{public_id}")
def activate_budget_router(
    public_id: str,
    body: BudgetActivate,
    current_user: dict = Depends(require_module_api(Modules.BUDGETS, "can_approve")),
):
    try:
        item = service.activate_by_public_id(
            public_id=public_id,
            row_version=body.row_version,
        )
    except ValueError as e:
        raise_workflow_error(str(e), "Failed to activate budget")
    if not item:
        raise_not_found("Budget")
    return item_response(item.to_dict())
