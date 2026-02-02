# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends, HTTPException, status

# Local Imports
from entities.project_address.api.schemas import ProjectAddressCreate, ProjectAddressUpdate
from entities.project_address.business.service import ProjectAddressService
from entities.auth.business.service import get_current_user_api
from workflows.workflow.api.router import TriggerRouter, TriggerContext, TriggerType, TriggerSource

router = APIRouter(prefix="/api/v1", tags=["api", "project_address"])


@router.post("/create/project_address")
def create_project_address_router(body: ProjectAddressCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new project address.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "project_id": body.project_id,
            "address_id": body.address_id,
            "address_type_id": body.address_type_id,
        },
        workflow_type="project_address_create",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create project address")
        )
    
    return result.get("data")


@router.get("/get/project_addresses")
def get_project_addresses_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all project addresses.
    """
    project_addresses = ProjectAddressService().read_all()
    return [project_address.to_dict() for project_address in project_addresses]


@router.get("/get/project_address/{public_id}")
def get_project_address_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a project address by public ID.
    """
    project_address = ProjectAddressService().read_by_public_id(public_id=public_id)
    return project_address.to_dict()


@router.get("/get/project_address/project/{project_id}")
def get_project_address_by_project_id_router(project_id: int, current_user: dict = Depends(get_current_user_api)):
    """
    Read project addresses by project ID.
    """
    project_addresses = ProjectAddressService().read_by_project_id(project_id=project_id)
    return [project_address.to_dict() for project_address in project_addresses]


@router.get("/get/project_address/address/{address_id}")
def get_project_address_by_address_id_router(address_id: int, current_user: dict = Depends(get_current_user_api)):
    """
    Read project addresses by address ID.
    """
    project_addresses = ProjectAddressService().read_by_address_id(address_id=address_id)
    return [project_address.to_dict() for project_address in project_addresses]


@router.get("/get/project_address/address_type/{address_type_id}")
def get_project_address_by_address_type_id_router(address_type_id: int, current_user: dict = Depends(get_current_user_api)):
    """
    Read project addresses by address type ID.
    """
    project_addresses = ProjectAddressService().read_by_address_type_id(address_type_id=address_type_id)
    return [project_address.to_dict() for project_address in project_addresses]


@router.put("/update/project_address/{public_id}")
def update_project_address_by_public_id_router(public_id: str, body: ProjectAddressUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update a project address by public ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
            "row_version": body.row_version,
            "project_id": body.project_id,
            "address_id": body.address_id,
            "address_type_id": body.address_type_id,
        },
        workflow_type="project_address_update",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to update project address")
        )
    
    return result.get("data")


@router.delete("/delete/project_address/{public_id}")
def delete_project_address_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a project address by public ID.
    
    Routes through the workflow engine for audit logging and state tracking.
    """
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=current_user.get("tenant_id", 1),
        user_id=current_user.get("id"),
        payload={
            "public_id": public_id,
        },
        workflow_type="project_address_delete",
    )
    
    result = TriggerRouter().route_instant(context)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete project address")
        )
    
    return result.get("data")
