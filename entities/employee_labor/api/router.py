# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends, Query

# Local Imports
from entities.employee_labor.api.schemas import EmployeeLaborCreate, EmployeeLaborUpdate
from entities.employee_labor.business.service import EmployeeLaborService
from shared.api.responses import list_response, item_response, raise_workflow_error, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel


router = APIRouter(prefix="/api/v1", tags=["api", "employee_labor"])
service = EmployeeLaborService()


def _dec(value):
    return str(value) if value is not None else None


@router.post("/create/employee-labor")
def create_employee_labor_router(
    body: EmployeeLaborCreate,
    current_user: dict = Depends(require_module_api(Modules.EMPLOYEE_LABOR, "can_create")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "employee_public_id": body.employee_public_id,
            "project_public_id": body.project_public_id,
            "work_date": body.work_date,
            "billing_period_start": body.billing_period_start,
            "billing_period_end": body.billing_period_end,
            "total_hours": _dec(body.total_hours),
            "hourly_rate": _dec(body.hourly_rate),
            "markup": _dec(body.markup),
            "total_amount": _dec(body.total_amount),
            "sub_cost_code_public_id": body.sub_cost_code_public_id,
            "description": body.description,
            "status": body.status,
            "source_time_entry_id": body.source_time_entry_id,
        },
        workflow_type="employee_labor_create",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create employee labor")
    return item_response(result.get("data"))


@router.get("/get/employee-labor/{public_id}")
def get_employee_labor_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.EMPLOYEE_LABOR)),
):
    item = service.read_by_public_id(public_id=public_id)
    if not item:
        raise_not_found("EmployeeLabor")
    return item_response(item.to_dict())


@router.get("/get/employee-labors")
def get_employee_labors_router(
    billing_period_start: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    current_user: dict = Depends(require_module_api(Modules.EMPLOYEE_LABOR)),
):
    if status:
        items = service.read_by_status(status, billing_period_start)
    elif billing_period_start:
        items = service.read_by_billing_period(billing_period_start)
    else:
        # No catch-all read_all to avoid scanning every row. Caller must
        # provide at least one filter.
        return list_response([])
    return list_response([i.to_dict() for i in items])


@router.put("/update/employee-labor/{public_id}")
def update_employee_labor_router(
    public_id: str,
    body: EmployeeLaborUpdate,
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
            "project_public_id": body.project_public_id,
            "total_hours": _dec(body.total_hours),
            "hourly_rate": _dec(body.hourly_rate),
            "markup": _dec(body.markup),
            "total_amount": _dec(body.total_amount),
            "sub_cost_code_public_id": body.sub_cost_code_public_id,
            "description": body.description,
            "status": body.status,
            "invoice_line_item_id": body.invoice_line_item_id,
        },
        workflow_type="employee_labor_update",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update employee labor")
    return item_response(result.get("data"))


@router.delete("/delete/employee-labor/{public_id}")
def delete_employee_labor_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.EMPLOYEE_LABOR, "can_delete")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={"public_id": public_id},
        workflow_type="employee_labor_delete",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete employee labor")
    return item_response(result.get("data"))
