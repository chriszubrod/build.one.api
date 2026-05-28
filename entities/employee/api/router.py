# Python Standard Library Imports
import asyncio

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from entities.employee.api.schemas import EmployeeCreate, EmployeeUpdate
from entities.employee.business.service import EmployeeService
from shared.api.responses import list_response, item_response, raise_workflow_error, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel

router = APIRouter(prefix="/api/v1", tags=["api", "employee"])
service = EmployeeService()


def _decimal_or_none(value):
    """Pydantic gives us Decimal; ProcessEngine serializes context to JSON, so
    convert to a string for transport. The service coerces back to Decimal."""
    return str(value) if value is not None else None


@router.post("/create/employee")
def create_employee_router(
    body: EmployeeCreate,
    current_user: dict = Depends(require_module_api(Modules.EMPLOYEES, "can_create")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "firstname": body.firstname,
            "lastname": body.lastname,
            "email": body.email,
            "hourly_rate": _decimal_or_none(body.hourly_rate),
            "markup": _decimal_or_none(body.markup),
            "is_active": body.is_active,
            "notes": body.notes,
        },
        workflow_type="employee_create",
    )

    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create employee")
    return item_response(result.get("data"))


@router.get("/get/employees")
async def get_employees_router(
    current_user: dict = Depends(require_module_api(Modules.EMPLOYEES)),
):
    employees = await asyncio.to_thread(service.read_all)
    return list_response([e.to_dict() for e in employees])


@router.get("/get/employee/{public_id}")
def get_employee_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.EMPLOYEES)),
):
    employee = service.read_by_public_id(public_id=public_id)
    if not employee:
        raise_not_found("Employee")
    return item_response(employee.to_dict())


@router.put("/update/employee/{public_id}")
def update_employee_by_public_id_router(
    public_id: str,
    body: EmployeeUpdate,
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
            "firstname": body.firstname,
            "lastname": body.lastname,
            "email": body.email,
            "hourly_rate": _decimal_or_none(body.hourly_rate),
            "markup": _decimal_or_none(body.markup),
            "is_active": body.is_active,
            "notes": body.notes,
        },
        workflow_type="employee_update",
    )

    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update employee")
    return item_response(result.get("data"))


@router.delete("/delete/employee/{public_id}")
def delete_employee_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.EMPLOYEES, "can_delete")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={"public_id": public_id},
        workflow_type="employee_delete",
    )

    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete employee")
    return item_response(result.get("data"))
