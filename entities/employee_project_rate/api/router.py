# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from entities.employee_project_rate.api.schemas import EmployeeProjectRateCreate, EmployeeProjectRateUpdate
from entities.employee_project_rate.business.service import EmployeeProjectRateService
from entities.employee.business.service import EmployeeService
from entities.project.business.service import ProjectService
from shared.api.responses import list_response, item_response, raise_workflow_error, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel


# Gated on Modules.EMPLOYEES — rate overrides are employee admin, not their own module.
router = APIRouter(prefix="/api/v1", tags=["api", "employee_project_rate"])
service = EmployeeProjectRateService()


def _decimal_or_none(value):
    return str(value) if value is not None else None


@router.post("/create/employee-project-rate")
def create_employee_project_rate_router(
    body: EmployeeProjectRateCreate,
    current_user: dict = Depends(require_module_api(Modules.EMPLOYEES, "can_update")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "employee_public_id": body.employee_public_id,
            "project_public_id": body.project_public_id,
            "hourly_rate": _decimal_or_none(body.hourly_rate),
            "markup": _decimal_or_none(body.markup),
            "notes": body.notes,
        },
        workflow_type="employee_project_rate_create",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create employee project rate")
    return item_response(result.get("data"))


@router.get("/get/employee-project-rate/{public_id}")
def get_employee_project_rate_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.EMPLOYEES)),
):
    item = service.read_by_public_id(public_id=public_id)
    if not item:
        raise_not_found("EmployeeProjectRate")
    return item_response(item.to_dict())


@router.get("/get/employee-project-rates/by-employee/{employee_public_id}")
def get_employee_project_rates_by_employee_router(
    employee_public_id: str,
    current_user: dict = Depends(require_module_api(Modules.EMPLOYEES)),
):
    """List per-project rate overrides for an employee — sub-section on EmployeeEdit."""
    employee = EmployeeService().read_by_public_id(public_id=employee_public_id)
    if not employee:
        raise_not_found("Employee")
    rates = service.read_by_employee_id(int(employee.id))
    return list_response([r.to_dict() for r in rates])


@router.get("/get/employee-project-rates/by-project/{project_public_id}")
def get_employee_project_rates_by_project_router(
    project_public_id: str,
    current_user: dict = Depends(require_module_api(Modules.EMPLOYEES)),
):
    """List per-employee rate overrides for a project — sub-section on ProjectEdit."""
    project = ProjectService().read_by_public_id(public_id=project_public_id)
    if not project:
        raise_not_found("Project")
    rates = service.read_by_project_id(int(project.id))
    return list_response([r.to_dict() for r in rates])


@router.put("/update/employee-project-rate/{public_id}")
def update_employee_project_rate_router(
    public_id: str,
    body: EmployeeProjectRateUpdate,
    current_user: dict = Depends(require_module_api(Modules.EMPLOYEES, "can_update")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "row_version": body.row_version,
            "hourly_rate": _decimal_or_none(body.hourly_rate),
            "markup": _decimal_or_none(body.markup),
            "notes": body.notes,
        },
        workflow_type="employee_project_rate_update",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update employee project rate")
    return item_response(result.get("data"))


@router.delete("/delete/employee-project-rate/{public_id}")
def delete_employee_project_rate_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.EMPLOYEES, "can_update")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={"public_id": public_id},
        workflow_type="employee_project_rate_delete",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete employee project rate")
    return item_response(result.get("data"))
