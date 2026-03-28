# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, HTTPException, Depends, status

# Local Imports
from entities.organization.business.service import OrganizationService
from entities.organization.api.schemas import OrganizationCreate, OrganizationUpdate
from entities.auth.business.service import get_current_user_api
from workflows.workflow.api.process_engine import ProcessEngine, TriggerContext, EventType, Channel

router = APIRouter(prefix="/api/v1", tags=["api", "organization"])
service = OrganizationService()


@router.post("/create/organization")
def create_organization_router(
        body: OrganizationCreate,
        current_user: dict = Depends(get_current_user_api),
    ):
    """
    Create a new organization.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "name": body.name,
            "website": body.website,
        },
        workflow_type="organization_create",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create organization")
        )
    
    return result.get("data")


@router.get("/get/organizations")
def get_organizations_router(
        current_user: dict = Depends(get_current_user_api),
    ):
    """
    Read all organizations.
    """
    _orgs = service.read_all()
    return [org.to_dict() for org in _orgs]


@router.get("/get/organization/{public_id}")
def get_organization_by_public_id_router(
        public_id: str,
        current_user: dict = Depends(get_current_user_api),
    ):
    """
    Read an organization by public ID.
    """
    _org = service.read_by_public_id(public_id=public_id)
    return _org.to_dict()


@router.put("/update/organization/{public_id}")
def update_organization_by_id_router(
        public_id: str,
        body: OrganizationUpdate,
        current_user: dict = Depends(get_current_user_api),
    ):
    """
    Update an organization by ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=EventType.API_CALL,
        trigger_source=Channel.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "row_version": body.row_version,
            "name": body.name,
            "website": body.website,
        },
        workflow_type="organization_update",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update organization")
        )
    
    return result.get("data")


@router.delete("/delete/organization/{public_id}")
def delete_organization_by_public_id_router(
        public_id: str,
        current_user: dict = Depends(get_current_user_api),
    ):
    """
    Soft delete an organization by ID.
    
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
        workflow_type="organization_delete",
    )
    
    result = ProcessEngine().execute_synchronous(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete organization")
        )
    
    return result.get("data")
