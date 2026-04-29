# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from entities.auth.business.service import get_current_user_api
from entities.user_organization.api.schemas import (
    UserOrganizationCreate,
    UserOrganizationUpdate,
)
from entities.user_organization.business.service import UserOrganizationService
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import (
    ProcessEngine,
    TriggerContext,
    EventType,
    Channel,
)
from shared.api.responses import list_response, item_response, raise_workflow_error

router = APIRouter(prefix="/api/v1", tags=["api", "user_organization"])


@router.post("/create/user_organization")
def create_user_organization_router(
    body: UserOrganizationCreate,
    current_user: dict = Depends(require_module_api(Modules.USERS, "can_create")),
):
    """Create a new user-organization assignment."""
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "user_id": body.user_id,
            "organization_id": body.organization_id,
        },
        workflow_type="user_organization_create",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create user organization")

    return item_response(result.get("data"))


@router.get("/get/user_organizations/user/{user_id}")
def get_user_organizations_by_user_id_router(
    user_id: int,
    current_user: dict = Depends(get_current_user_api),
):
    """Read all UserOrganization rows for the user. Auth-only (matches UserRole convention)."""
    rows = UserOrganizationService().read_all_by_user_id(user_id=user_id)
    return list_response([row.to_dict() for row in rows])


@router.get("/get/user_organizations")
def get_user_organizations_router(
    current_user: dict = Depends(require_module_api(Modules.USERS)),
):
    """Read all user-organization assignments."""
    rows = UserOrganizationService().read_all()
    return list_response([row.to_dict() for row in rows])


@router.get("/get/user_organization/{public_id}")
def get_user_organization_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.USERS)),
):
    """Read a user-organization assignment by public ID."""
    row = UserOrganizationService().read_by_public_id(public_id=public_id)
    return item_response(row.to_dict() if row else None)


@router.put("/update/user_organization/{public_id}")
def update_user_organization_by_public_id_router(
    public_id: str,
    body: UserOrganizationUpdate,
    current_user: dict = Depends(require_module_api(Modules.USERS, "can_update")),
):
    """Update a user-organization assignment by public ID."""
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "row_version": body.row_version,
            "user_id": body.user_id,
            "organization_id": body.organization_id,
        },
        workflow_type="user_organization_update",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update user organization")

    return item_response(result.get("data"))


@router.delete("/delete/user_organization/{public_id}")
def delete_user_organization_by_public_id_router(
    public_id: str,
    current_user: dict = Depends(require_module_api(Modules.USERS, "can_delete")),
):
    """Delete a user-organization assignment by public ID."""
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
        },
        workflow_type="user_organization_delete",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete user organization")

    return item_response(result.get("data"))
