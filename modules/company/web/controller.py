# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from modules.company.business.service import CompanyService
from modules.auth.business.service import get_current_user_web

router = APIRouter(prefix="/company", tags=["web", "company"])
templates = Jinja2Templates(directory="templates/company")


@router.get("/list")
async def list_companies(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    List all companies.
    """
    companies = CompanyService().read_all()
    return templates.TemplateResponse(
        "list.html",
        {
            "request": request,
            "companies": companies,
            "current_user": current_user,
        },
    )


@router.get("/create")
async def create_company(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Render create company form.
    """
    return templates.TemplateResponse(
        "create.html",
        {
            "request": request,
            "current_user": current_user,
        },
    )


@router.get("/{public_id}")
async def view_company(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    View a company.
    """
    company = CompanyService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
     "view.html",
        {
            "request": request,
            "company": company.to_dict(),
            "current_user": current_user,
        },
    )


@router.get("/{public_id}/edit")
async def edit_company(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    Edit a company.
    """
    company = CompanyService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "company": company.to_dict(),
            "current_user": current_user,
        },
    )
