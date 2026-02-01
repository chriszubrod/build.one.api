# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from services.organization.business.service import OrganizationService
from services.auth.business.service import get_current_user_web

router = APIRouter(prefix="/organization", tags=["web", "organization"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_organizations(
    request: Request,
    current_user: dict = Depends(get_current_user_web)
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
    current_user: dict = Depends(get_current_user_web)
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
    current_user: dict = Depends(get_current_user_web)
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
    current_user: dict = Depends(get_current_user_web)
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
