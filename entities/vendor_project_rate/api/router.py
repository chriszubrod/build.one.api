# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from entities.vendor_project_rate.api.schemas import VendorProjectRateCreate, VendorProjectRateUpdate
from entities.vendor_project_rate.business.service import VendorProjectRateService
from entities.vendor.business.service import VendorService
from entities.project.business.service import ProjectService
from shared.api.responses import list_response, item_response, raise_workflow_error, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel


# Gated on Modules.VENDORS — rate overrides are vendor admin, not their own module.
router = APIRouter(prefix="/api/v1", tags=["api", "vendor_project_rate"])
service = VendorProjectRateService()


def _decimal_or_none(value):
    return str(value) if value is not None else None


@router.post("/create/vendor-project-rate")
def create_vendor_project_rate_router(
    body: VendorProjectRateCreate,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_update")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "vendor_public_id": body.vendor_public_id,
            "project_public_id": body.project_public_id,
            "hourly_rate": _decimal_or_none(body.hourly_rate),
            "markup": _decimal_or_none(body.markup),
            "notes": body.notes,
        },
        workflow_type="vendor_project_rate_create",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create vendor project rate")
    return item_response(result.get("data"))


@router.get("/get/vendor-project-rate/{public_id}")
def get_vendor_project_rate_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.VENDORS)),
):
    item = service.read_by_public_id(public_id=public_id)
    if not item:
        raise_not_found("VendorProjectRate")
    return item_response(item.to_dict())


@router.get("/get/vendor-project-rates/by-vendor/{vendor_public_id}")
def get_vendor_project_rates_by_vendor_router(
    vendor_public_id: str,
    current_user: dict = Depends(require_module_api(Modules.VENDORS)),
):
    """List per-project rate overrides for a vendor — sub-section on VendorEdit."""
    vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
    if not vendor:
        raise_not_found("Vendor")
    rates = service.read_by_vendor_id(int(vendor.id))
    return list_response([r.to_dict() for r in rates])


@router.get("/get/vendor-project-rates/by-project/{project_public_id}")
def get_vendor_project_rates_by_project_router(
    project_public_id: str,
    current_user: dict = Depends(require_module_api(Modules.VENDORS)),
):
    """List per-vendor rate overrides for a project — sub-section on ProjectEdit."""
    project = ProjectService().read_by_public_id(public_id=project_public_id)
    if not project:
        raise_not_found("Project")
    rates = service.read_by_project_id(int(project.id))
    return list_response([r.to_dict() for r in rates])


@router.put("/update/vendor-project-rate/{public_id}")
def update_vendor_project_rate_router(
    public_id: str,
    body: VendorProjectRateUpdate,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_update")),
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
        workflow_type="vendor_project_rate_update",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update vendor project rate")
    return item_response(result.get("data"))


@router.delete("/delete/vendor-project-rate/{public_id}")
def delete_vendor_project_rate_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.VENDORS, "can_update")),
):
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={"public_id": public_id},
        workflow_type="vendor_project_rate_delete",
    )
    result = ProcessEngine().execute_synchronous(context)
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete vendor project rate")
    return item_response(result.get("data"))
