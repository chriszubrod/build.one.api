# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.company.business.service import CompanyService
from entities.auth.business.service import get_current_user_web
from integrations.ms.sharepoint.drive.connector.company.business.service import DriveCompanyConnector

router = APIRouter(prefix="/company", tags=["web", "company"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_companies(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    List all companies.
    """
    companies = CompanyService().read_all()
    return templates.TemplateResponse(
        "company/list.html",
        {
            "request": request,
            "companies": companies,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/create")
async def create_company(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Render create company form.
    """
    return templates.TemplateResponse(
        "company/create.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}")
async def view_company(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    View a company.
    """
    company = CompanyService().read_by_public_id(public_id=public_id)
    
    # Get linked drive if any
    linked_drive = None
    if company and company.id:
        connector = DriveCompanyConnector()
        linked_drive = connector.get_drive_for_company(company_id=int(company.id))
    
    return templates.TemplateResponse(
        "company/view.html",
        {
            "request": request,
            "company": company.to_dict(),
            "linked_drive": linked_drive,  # Already a dict from get_drive_for_company
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_company(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    Edit a company.
    """
    company = CompanyService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "company/edit.html",
        {
            "request": request,
            "company": company.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
