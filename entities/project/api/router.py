# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.project.api.schemas import ProjectCreate, ProjectUpdate
from entities.project.business.service import ProjectService
from entities.auth.business.service import get_current_user_api
from workflows.workflow.api.router import TriggerRouter, TriggerContext, TriggerType, TriggerSource

router = APIRouter(prefix="/api/v1", tags=["api", "project"])


@router.post("/create/project")
def create_project_router(body: ProjectCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new project.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    # Create trigger context for the instant workflow
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "name": body.name,
            "description": body.description,
            "status": body.status,
            "customer_id": body.customer_id,
            "abbreviation": body.abbreviation,
        },
        workflow_type="project_create",
    )
    
    # Route through workflow engine
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create project")
        )
    
    return result.get("data")


@router.get("/get/projects")
def get_projects_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all projects.
    """
    projects = ProjectService().read_all()
    return [project.to_dict() for project in projects]


@router.get("/get/project/{public_id}")
def get_project_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a project by public ID.
    """
    project = ProjectService().read_by_public_id(public_id=public_id)
    return project.to_dict()


@router.put("/update/project/{public_id}")
def update_project_by_public_id_router(public_id: str, body: ProjectUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update a project by public ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    # Create trigger context for the instant workflow
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "row_version": body.row_version,
            "name": body.name,
            "description": body.description,
            "status": body.status,
            "customer_id": body.customer_id,
            "abbreviation": body.abbreviation,
        },
        workflow_type="project_update",
    )
    
    # Route through workflow engine
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update project")
        )
    
    return result.get("data")


@router.delete("/delete/project/{public_id}")
def delete_project_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a project by public ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    # Create trigger context for the instant workflow
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
        },
        workflow_type="project_delete",
    )
    
    # Route through workflow engine
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete project")
        )
    
    return result.get("data")
