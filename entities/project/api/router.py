# Python Standard Library Imports
import asyncio

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.project.api.schemas import ProjectCreate, ProjectUpdate
from entities.project.business.service import ProjectService
from entities.customer.business.service import CustomerService
from shared.api.responses import list_response, item_response, raise_workflow_error, raise_not_found
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from core.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel

router = APIRouter(prefix="/api/v1", tags=["api", "project"])
customer_service = CustomerService()


def _resolve_customer_id(customer_public_id: str | None) -> int | None:
    """Resolve a customer public_id to its internal id. Returns None if input is None."""
    if not customer_public_id:
        return None
    customer = customer_service.read_by_public_id(customer_public_id)
    if not customer:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Customer not found: {customer_public_id}",
        )
    return customer.id


@router.post("/create/project")
def create_project_router(body: ProjectCreate, current_user: dict = Depends(require_module_api(Modules.PROJECTS, "can_create"))):
    """
    Create a new project.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    customer_id = _resolve_customer_id(body.customer_public_id)

    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "name": body.name,
            "description": body.description,
            "status": body.status,
            "customer_id": customer_id,
            "abbreviation": body.abbreviation,
        },
        workflow_type="project_create",
    )
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to create project")

    return item_response(result.get("data"))


@router.get("/get/projects")
async def get_projects_router(current_user: dict = Depends(require_module_api(Modules.PROJECTS))):
    """
    Read all projects.
    """
    projects = await asyncio.to_thread(ProjectService().read_all)
    return list_response([project.to_dict() for project in projects])


@router.get("/get/project/search")
def search_projects_router(
    q: str,
    limit: int = 10,
    current_user: dict = Depends(require_module_api(Modules.PROJECTS)),
):
    """
    Case-insensitive substring search over Project name + abbreviation.
    Returns up to `limit` matches (default 10). Intended for agent
    narrow-lookup and dropdown search — cheaper than listing the full
    catalog when only a few rows matter.
    """
    if limit < 1:
        limit = 1
    if limit > 100:
        limit = 100
    matches = ProjectService().search_by_name(query=q, limit=limit)
    return list_response([p.to_dict() for p in matches])


@router.get("/get/project/by-customer/{customer_id}")
def get_projects_by_customer_router(
    customer_id: int,
    current_user: dict = Depends(require_module_api(Modules.PROJECTS)),
):
    """
    Return all projects belonging to a Customer (BIGINT FK).

    Intended for the Customer specialist agent's "what projects does X
    have?" query, where it has the parent's internal id from a previous
    Customer read.
    """
    projects = ProjectService().read_by_customer_id(customer_id=customer_id)
    return list_response([p.to_dict() for p in projects])


@router.get("/get/project/{public_id}")
def get_project_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.PROJECTS))):
    """
    Read a project by public ID.
    """
    project = ProjectService().read_by_public_id(public_id=public_id)
    if not project:
        raise_not_found("Project")
    return item_response(project.to_dict())


@router.put("/update/project/{public_id}")
def update_project_by_public_id_router(public_id: str, body: ProjectUpdate, current_user: dict = Depends(require_module_api(Modules.PROJECTS, "can_update"))):
    """
    Update a project by public ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    customer_id = _resolve_customer_id(body.customer_public_id)

    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "row_version": body.row_version,
            "name": body.name,
            "description": body.description,
            "status": body.status,
            "customer_id": customer_id,
            "abbreviation": body.abbreviation,
        },
        workflow_type="project_update",
    )
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to update project")

    return item_response(result.get("data"))


@router.delete("/delete/project/{public_id}")
def delete_project_by_public_id_router(public_id: str, current_user: dict = Depends(require_module_api(Modules.PROJECTS, "can_delete"))):
    """
    Delete a project by public ID.

    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
        },
        workflow_type="project_delete",
    )

    result = ProcessEngine().execute_synchronous(context)

    if not result.get("success"):
        raise_workflow_error(result.get("error", ""), "Failed to delete project")

    return item_response(result.get("data"))
