# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from entities.organization_company.api.schemas import (
    OrganizationCompanyCreate,
    OrganizationCompanyUpdate,
)
from entities.organization_company.business.service import OrganizationCompanyService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import (
    ProcessEngine,
    TriggerContext,
    EventType,
    Channel,
)
from shared.api.responses import list_response, item_response, raise_workflow_error

router = APIRouter(prefix="/api/v1", tags=["api", "organization_company"])


@router.post("/create/organization_company")
def create_organization_company_router(
    body: OrganizationCompanyCreate,
    current_user: dict = Depends(require_module_api(Modules.ORGANIZATIONS, "can_create")),
):
    """Create a new organization-company link."""
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "organization_id": body.organization_id,
            "company_id": body.company_id,
        },
        workflow_type="organization_company_create",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create organization company")

    return item_response(result.get("data"))


@router.get("/get/organization_companies")
def get_organization_companies_router(
    current_user: dict = Depends(require_module_api(Modules.ORGANIZATIONS)),
):
    """Read all organization-company links."""
    rows = OrganizationCompanyService().read_all()
    return list_response([row.to_dict() for row in rows])


@router.get("/get/organization_company/{public_id}")
def get_organization_company_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.ORGANIZATIONS)),
):
    """Read an organization-company link by public ID."""
    row = OrganizationCompanyService().read_by_public_id(public_id=public_id)
    return item_response(row.to_dict() if row else None)


@router.get("/get/organization_companies/organization/{organization_id}")
def get_organization_companies_by_organization_id_router(
    organization_id: int,
    current_user: dict = Depends(require_module_api(Modules.ORGANIZATIONS)),
):
    """Read all OrganizationCompany rows for the organization."""
    rows = OrganizationCompanyService().read_all_by_organization_id(
        organization_id=organization_id
    )
    return list_response([row.to_dict() for row in rows])


@router.get("/get/organization_companies/company/{company_id}")
def get_organization_companies_by_company_id_router(
    company_id: int,
    current_user: dict = Depends(require_module_api(Modules.ORGANIZATIONS)),
):
    """Read all OrganizationCompany rows for the company."""
    rows = OrganizationCompanyService().read_all_by_company_id(company_id=company_id)
    return list_response([row.to_dict() for row in rows])


@router.put("/update/organization_company/{public_id}")
def update_organization_company_by_public_id_router(
    public_id: str,
    body: OrganizationCompanyUpdate,
    current_user: dict = Depends(require_module_api(Modules.ORGANIZATIONS, "can_update")),
):
    """Update an organization-company link by public ID."""
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "row_version": body.row_version,
            "organization_id": body.organization_id,
            "company_id": body.company_id,
        },
        workflow_type="organization_company_update",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update organization company")

    return item_response(result.get("data"))


@router.delete("/delete/organization_company/{public_id}")
def delete_organization_company_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.ORGANIZATIONS, "can_delete")),
):
    """Delete an organization-company link by public ID."""
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
        },
        workflow_type="organization_company_delete",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete organization company")

    return item_response(result.get("data"))
