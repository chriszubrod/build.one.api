# Python Standard Library Imports
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.auth.business.service import QboAuthService
from modules.auth.business.service import get_current_user_web as get_current_qbo_auth_web

router = APIRouter(prefix="/qbo-auth", tags=["web", "qbo-auth"])
templates = Jinja2Templates(directory="templates/qbo-auth")


@router.get("/list")
async def list_qbo_auths(request: Request, current_user: dict = Depends(get_current_qbo_auth_web)):
    """
    List all QBO auths.
    """
    qbo_auths = QboAuthService().read_all()
    return templates.TemplateResponse(
        "list.html",
        {
            "request": request,
            "qbo_auths": qbo_auths,
            "current_user": current_user,
        },
    )


@router.get("/create")
async def create_qbo_auth(request: Request, current_user: dict = Depends(get_current_qbo_auth_web)):
    """
    Render create QBO auth form.
    """
    return templates.TemplateResponse(
        "create.html",
        {
            "request": request,
            "current_user": current_user,
        },
    )


@router.get("/{realm_id}")
async def view_qbo_auth(request: Request, realm_id: str, current_user: dict = Depends(get_current_qbo_auth_web)):
    """
    View a QBO auth.
    """
    qbo_auth = QboAuthService().read_by_realm_id(realm_id)
    return templates.TemplateResponse(
        "view.html",
        {
            "request": request,
            "qbo_auth": qbo_auth.to_dict(),
            "current_user": current_user,
        },
    )
