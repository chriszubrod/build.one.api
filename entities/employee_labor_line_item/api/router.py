# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from entities.employee_labor_line_item.api.schemas import EmployeeLaborLineItemCreate, EmployeeLaborLineItemUpdate
from entities.employee_labor_line_item.business.service import EmployeeLaborLineItemService
from entities.employee_labor.business.service import EmployeeLaborService
from shared.api.responses import list_response, item_response, raise_workflow_error, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel


router = APIRouter(prefix="/api/v1", tags=["api", "employee_labor_line_item"])
service = EmployeeLaborLineItemService()


def _dec(value):
    return str(value) if value is not None else None


@router.post("/create/employee-labor-line-item")
def create_employee_labor_line_item_router(
    body: EmployeeLaborLineItemCreate,
    current_user: dict = Depends(require_module_api(Modules.EMPLOYEE_LABOR, "can_create")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "employee_labor_public_id": body.employee_labor_public_id,
            "line_date": body.line_date,
            "project_public_id": body.project_public_id,
            "sub_cost_code_public_id": body.sub_cost_code_public_id,
            "description": body.description,
            "hours": _dec(body.hours),
            "rate": _dec(body.rate),
            "markup": _dec(body.markup),
            "price": _dec(body.price),
            "is_billable": body.is_billable,
            "is_overhead": body.is_overhead,
        },
        workflow_type="employee_labor_line_item_create",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create line item")
    return item_response(result.get("data"))


@router.get("/get/employee-labor-line-item/{public_id}")
def get_employee_labor_line_item_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.EMPLOYEE_LABOR)),
):
    item = service.read_by_public_id(public_id=public_id)
    if not item:
        raise_not_found("EmployeeLaborLineItem")
    return item_response(item.to_dict())


@router.get("/get/employee-labor-line-items/by-parent/{employee_labor_public_id}")
def get_employee_labor_line_items_by_parent_router(
    employee_labor_public_id: str,
    current_user: dict = Depends(require_module_api(Modules.EMPLOYEE_LABOR)),
):
    parent = EmployeeLaborService().read_by_public_id(public_id=employee_labor_public_id)
    if not parent:
        raise_not_found("EmployeeLabor")
    items = service.read_by_employee_labor_id(int(parent.id))
    return list_response([i.to_dict() for i in items])


@router.put("/update/employee-labor-line-item/{public_id}")
def update_employee_labor_line_item_router(
    public_id: str,
    body: EmployeeLaborLineItemUpdate,
    current_user: dict = Depends(require_module_api(Modules.EMPLOYEE_LABOR, "can_update")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "row_version": body.row_version,
            "line_date": body.line_date,
            "project_public_id": body.project_public_id,
            "sub_cost_code_public_id": body.sub_cost_code_public_id,
            "description": body.description,
            "hours": _dec(body.hours),
            "rate": _dec(body.rate),
            "markup": _dec(body.markup),
            "price": _dec(body.price),
            "is_billable": body.is_billable,
            "is_overhead": body.is_overhead,
            "invoice_line_item_id": body.invoice_line_item_id,
        },
        workflow_type="employee_labor_line_item_update",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update line item")
    return item_response(result.get("data"))


@router.delete("/delete/employee-labor-line-item/{public_id}")
def delete_employee_labor_line_item_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.EMPLOYEE_LABOR, "can_delete")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={"public_id": public_id},
        workflow_type="employee_labor_line_item_delete",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete line item")
    return item_response(result.get("data"))
