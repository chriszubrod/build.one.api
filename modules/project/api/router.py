# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from modules.project.api.schemas import ProjectCreate, ProjectUpdate
from modules.project.business.service import ProjectService
from modules.auth.business.service import get_current_user_api

router = APIRouter(prefix="/api/v1", tags=["api", "project"])


@router.post("/create/project")
def create_project_router(body: ProjectCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new project.
    """
    project = ProjectService().create(
        name=body.name,
        description=body.description,
        status=body.status,
        customer_id=body.customer_id,
        abbreviation=body.abbreviation,
    )
    return project.to_dict()


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
    """
    project = ProjectService().update_by_public_id(public_id=public_id, project=body)
    return project.to_dict()


@router.delete("/delete/project/{public_id}")
def delete_project_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a project by public ID.
    """
    project = ProjectService().delete_by_public_id(public_id=public_id)
    return project.to_dict()
