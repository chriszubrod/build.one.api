# Python Standard Library Imports
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.company_info.business.service import QboCompanyInfoService
from integrations.intuit.qbo.auth.business.service import QboAuthService
from services.auth.business.service import get_current_user_web as get_current_qbo_company_info_web

router = APIRouter(prefix="/qbo-company-info", tags=["web", "qbo-company-info"])
templates = Jinja2Templates(directory="templates/qbo-company-info")


@router.get("")
async def view_qbo_company_info(request: Request, current_user: dict = Depends(get_current_qbo_company_info_web)):
    """
    View CompanyInfo for the current user's realm.
    """
    # Get realm_id from auth
    auth_service = QboAuthService()
    all_auths = auth_service.read_all()
    
    if not all_auths or len(all_auths) == 0:
        return templates.TemplateResponse(
            "error.html",
            {
                "request": request,
                "error_message": "No QBO authentication found. Please connect your QuickBooks account first.",
                "current_user": current_user,
            },
        )
    
    realm_id = all_auths[0].realm_id
    company_info_service = QboCompanyInfoService()
    company_info = company_info_service.read_by_realm_id(realm_id=realm_id)
    
    return templates.TemplateResponse(
        "view.html",
        {
            "request": request,
            "company_info": company_info.to_dict() if company_info else None,
            "current_user": current_user,
        },
    )


@router.get("/sync")
async def sync_qbo_company_info(request: Request, current_user: dict = Depends(get_current_qbo_company_info_web)):
    """
    Trigger sync of CompanyInfo from QBO.
    """
    # Get realm_id from auth
    auth_service = QboAuthService()
    all_auths = auth_service.read_all()
    
    if not all_auths or len(all_auths) == 0:
        return templates.TemplateResponse(
            "error.html",
            {
                "request": request,
                "error_message": "No QBO authentication found. Please connect your QuickBooks account first.",
                "current_user": current_user,
            },
        )
    
    realm_id = all_auths[0].realm_id
    company_info_service = QboCompanyInfoService()
    
    try:
        company_info = company_info_service.sync_from_qbo(realm_id=realm_id)
        return templates.TemplateResponse(
            "sync.html",
            {
                "request": request,
                "company_info": company_info.to_dict(),
                "success": True,
                "current_user": current_user,
            },
        )
    except Exception as e:
        return templates.TemplateResponse(
            "sync.html",
            {
                "request": request,
                "error_message": str(e),
                "success": False,
                "current_user": current_user,
            },
        )

