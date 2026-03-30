# Python Standard Library Imports
import logging
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from entities.company.business.service import CompanyService
from entities.contact.business.service import ContactService
from shared.rbac import require_module_web
from shared.rbac_constants import Modules
from integrations.ms.sharepoint.drive.connector.company.business.service import DriveCompanyConnector
from integrations.ms.sharepoint.driveitem.connector.bill_folder.business.service import DriveItemBillFolderConnector
from integrations.ms.sharepoint.driveitem.connector.expense_folder.business.service import DriveItemExpenseFolderConnector

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/company", tags=["web", "company"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_companies(request: Request, current_user: dict = Depends(require_module_web(Modules.COMPANIES))):
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
async def create_company(request: Request, current_user: dict = Depends(require_module_web(Modules.COMPANIES, "can_create"))):
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
async def view_company(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.COMPANIES))):
    """
    View a company.
    """
    company = CompanyService().read_by_public_id(public_id=public_id)
    
    # Get linked drive if any
    linked_drive = None
    if company and company.id:
        connector = DriveCompanyConnector()
        linked_drive = connector.get_drive_for_company(company_id=int(company.id))

    # Get bill processing folder links
    bill_source_folder = None
    bill_processed_folder = None
    if company and company.id:
        try:
            bill_connector = DriveItemBillFolderConnector()
            bill_source_folder = bill_connector.get_folder(int(company.id), "source")
            bill_processed_folder = bill_connector.get_folder(int(company.id), "processed")
        except Exception:
            logger.debug("Bill folder connector not available for company %s", company.id)

    # Get expense processing folder links
    expense_source_folder = None
    expense_processed_folder = None
    if company and company.id:
        try:
            expense_connector = DriveItemExpenseFolderConnector()
            expense_source_folder = expense_connector.get_folder(int(company.id), "source")
            expense_processed_folder = expense_connector.get_folder(int(company.id), "processed")
        except Exception:
            logger.debug("Expense folder connector not available for company %s", company.id)

    contacts = ContactService().read_by_company_id(company_id=company.id)
    return templates.TemplateResponse(
        "company/view.html",
        {
            "request": request,
            "company": company.to_dict(),
            "linked_drive": linked_drive,
            "bill_source_folder": bill_source_folder,
            "bill_processed_folder": bill_processed_folder,
            "expense_source_folder": expense_source_folder,
            "expense_processed_folder": expense_processed_folder,
            "contacts": [c.to_dict() for c in contacts],
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_company(request: Request, public_id: str, current_user: dict = Depends(require_module_web(Modules.COMPANIES, "can_update"))):
    """
    Edit a company.
    """
    company = CompanyService().read_by_public_id(public_id=public_id)
    contacts = ContactService().read_by_company_id(company_id=company.id)
    return templates.TemplateResponse(
        "company/edit.html",
        {
            "request": request,
            "company": company.to_dict(),
            "contacts": [c.to_dict() for c in contacts],
            "parent_entity": "company",
            "parent_id": company.id,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
