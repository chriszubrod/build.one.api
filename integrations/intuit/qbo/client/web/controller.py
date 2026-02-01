# Python Standard Library Imports
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.client.business.service import QboClientService
from services.auth.business.service import get_current_user_web as get_current_qbo_client_web

router = APIRouter(prefix="/qbo-client", tags=["web", "qbo-client"])
templates = Jinja2Templates(directory="templates/qbo-client")


@router.get("/list")
async def list_qbo_clients(request: Request, current_user: dict = Depends(get_current_qbo_client_web)):
    """
    List all QBO clients by app.
    """
    qbo_clients = QboClientService().read_all()
    return templates.TemplateResponse(
        "list.html",
        {
            "request": request,
            "qbo_clients": qbo_clients,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/create")
async def create_qbo_client(request: Request, current_user: dict = Depends(get_current_qbo_client_web)):
    """
    Render create QBO client form.
    """
    return templates.TemplateResponse(
        "create.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{app}")
async def view_qbo_client(request: Request, app: str, current_user: dict = Depends(get_current_qbo_client_web)):
    """
    View a QBO client by app.
    """
    qbo_client = QboClientService().read_by_app(app)
    return templates.TemplateResponse(
        "view.html",
        {
            "request": request,
            "qbo_client": qbo_client.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{app}/edit")
async def edit_qbo_client(request: Request, app: str, current_user: dict = Depends(get_current_qbo_client_web)):
    """
    Edit a QBO client by app.
    """
    qbo_client = QboClientService().read_by_app(app)
    return templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "qbo_client": qbo_client.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
