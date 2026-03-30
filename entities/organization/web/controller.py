# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.organization.business.service import OrganizationService
from shared.rbac import require_module_web
from shared.rbac_constants import Modules

router = APIRouter(prefix="/organization", tags=["web", "organization"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_organizations(
    request: Request,
    current_user: dict = Depends(require_module_web(Modules.ORGANIZATIONS))
    ):
    """
    List all organizations.
    """
    organizations = OrganizationService().read_all()
    return templates.TemplateResponse(
        "organization/list.html",
        {
            "request": request,
            "organizations": organizations,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/create")
async def create_organization(
    request: Request,
    current_user: dict = Depends(require_module_web(Modules.ORGANIZATIONS, "can_create"))
    ):
    """
    Render create organization form.
    """
    return templates.TemplateResponse(
        "organization/create.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}")
async def view_organization(
    request: Request,
    public_id: str,
    current_user: dict = Depends(require_module_web(Modules.ORGANIZATIONS))
    ):
    """
    View a organization.
    """
    organization = OrganizationService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "organization/view.html",
        {
            "request": request,
            "organization": organization.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_organization(
    request: Request,
    public_id: str,
    current_user: dict = Depends(require_module_web(Modules.ORGANIZATIONS, "can_update"))
    ):
    """
    Edit a organization.
    """
    organization = OrganizationService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "organization/edit.html",
        {
            "request": request,
            "organization": organization.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
