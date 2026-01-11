# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from modules.project.business.service import ProjectService
from modules.auth.business.service import get_current_user_web

router = APIRouter(prefix="/project", tags=["web", "project"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_projects(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    List all projects.
    """
    projects = ProjectService().read_all()
    return templates.TemplateResponse(
        "project/list.html",
        {
            "request": request,
            "projects": projects,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/create")
async def create_project(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Render create project form.
    """
    return templates.TemplateResponse(
        "project/create.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}")
async def view_project(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    View a project.
    """
    project = ProjectService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "project/view.html",
        {
            "request": request,
            "project": project.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_project(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    Edit a project.
    """
    project = ProjectService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "project/edit.html",
        {
            "request": request,
            "project": project.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
