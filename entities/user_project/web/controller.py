# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.user_project.business.service import UserProjectService
from entities.user.business.service import UserService
from entities.project.business.service import ProjectService
from shared.rbac import require_module_web
from shared.rbac_constants import Modules

router = APIRouter(prefix="/user_project", tags=["web", "user_project"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_user_projects(request: Request, current_user: dict = Depends(require_module_web(Modules.PROJECTS))):
    """
    List all user projects.
    """
    user_projects = UserProjectService().read_all()
    users = UserService().read_all()
    projects = ProjectService().read_all()
    user_map = {u.id: f"{u.firstname} {u.lastname or ''}".strip() for u in users}
    project_map = {p.id: p.name for p in projects}
    return templates.TemplateResponse(
        "user_project/list.html",
        {
            "request": request,
            "user_projects": user_projects,
            "user_map": user_map,
            "project_map": project_map,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/create")
async def create_user_project(request: Request, current_user: dict = Depends(require_module_web(Modules.PROJECTS, "can_create"))):
    """
    Render create user project form.
    """
    users = UserService().read_all()
    projects = ProjectService().read_all()
    return templates.TemplateResponse(
        "user_project/create.html",
        {
            "request": request,
            "users": [u.to_dict() for u in users],
            "projects": [p.to_dict() for p in projects],
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}")
async def view_user_project(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.PROJECTS))):
    """
    View a user project.
    """
    user_project = UserProjectService().read_by_public_id(public_id=public_id)
    users = UserService().read_all()
    projects = ProjectService().read_all()
    user_map = {u.id: f"{u.firstname} {u.lastname or ''}".strip() for u in users}
    project_map = {p.id: p.name for p in projects}
    return templates.TemplateResponse(
        "user_project/view.html",
        {
            "request": request,
            "user_project": user_project.to_dict(),
            "user_map": user_map,
            "project_map": project_map,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_user_project(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.PROJECTS, "can_update"))):
    """
    Edit a user project.
    """
    user_project = UserProjectService().read_by_public_id(public_id=public_id)
    users = UserService().read_all()
    projects = ProjectService().read_all()
    return templates.TemplateResponse(
        "user_project/edit.html",
        {
            "request": request,
            "user_project": user_project.to_dict(),
            "users": [u.to_dict() for u in users],
            "projects": [p.to_dict() for p in projects],
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
