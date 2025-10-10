# Python Standard Library Imports
from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from modules.organization.business.service import OrganizationService

router = APIRouter(prefix="/organization", tags=["web", "organization"])
service = OrganizationService()
templates = Jinja2Templates(directory="templates/organization")


@router.get("/list")
async def list_organizations(request: Request):
    """
    Get all organizations.
    """
    _orgs = service.read_all()
    return templates.TemplateResponse(
        "list.html",
        {
            "request": request,
            "orgs": _orgs
        }
    )


@router.get("/create")
async def create_organization(request: Request):
    """
    Create an organization.
    """
    return templates.TemplateResponse(
        "create.html",
        {
            "request": request
        }
    )


@router.get("/{public_id}")
async def view_organization(request: Request, public_id: str):
    """
    View an organization.
    """
    _org = service.read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "view.html",
        {
            "request": request,
            "org": _org.to_dict()
        }
    )


@router.get("/{public_id}/edit")
async def edit_organization(request: Request, public_id: str):
    """
    Edit an organization.
    """
    _org = service.read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "org": _org.to_dict()
        }
    )
